# -*- Mode: Python -*-

from pdb import set_trace as trace
from pprint import pprint as pp

import nodes
#import solver
import itypes

is_a = isinstance

class register_rib:
    def __init__ (self, formals, regs):
        self.formals = formals
        self.regs = regs
        assert (len (formals) == len (regs))
    def lookup (self, name):
        lf = len (self.formals)
        for i in range (lf):
            if name == self.formals[i].name:
                return self.formals[i], self.regs[i]
        return None
    def __repr__ (self):
        return '<reg: %r %r>' % (self.formals, self.regs)

class fatbar_rib:
    def __init__ (self, name):
        self.name = name

class IncompleteMatch (Exception):
    pass

class compiler:

    def __init__ (self, context, verbose=False):
        self.context = context
        self.verbose = verbose
        self.constants = {}
        self.regalloc = register_allocator()
        self.current_function = None

    def lexical_address (self, lenv, name):
        x = 0
        while lenv:
            rib, lenv = lenv
            if is_a (rib, register_rib):
                probe = rib.lookup (name)
                if probe is not None:
                    var, reg = probe
                    return var, (None, reg), False
            elif is_a (rib, fatbar_rib):
                # ignore these for normal variable lookup
                pass
            else:
                for y in range (len (rib)):
                    if rib[y].name == name:
                        return rib[y], (x, y), self.use_top and lenv == None
                # only real 'ribs' increase lexical depth
                x += 1
        else:
            raise ValueError, "unbound variable: %r" % (name,)

    # This 'compiler' converts <exp> to CPS, with each continuation representing
    #  a target 'register' for the result of that expression.

    def compile_exp (self, tail_pos, exp, lenv, k):

        #import sys; W = sys.stdout.write
        #W ('compile_exp: [%3d] %r\n' % (exp.serial, exp,))
        if tail_pos:
            k = self.cont (k[1], self.gen_return)
        
        if exp.is_a ('varref'):
            return self.compile_varref (tail_pos, exp, lenv, k)
        elif exp.is_a ('varset'):
            return self.compile_varset (tail_pos, exp, lenv, k)
        elif exp.is_a ('literal'):
            return self.compile_literal (tail_pos, exp, lenv, k)            
        elif exp.is_a ('constructed'):
            return self.gen_constructed (self.scan_constructed (exp.value), k)
        elif exp.is_a ('sequence'):
            return self.compile_sequence (tail_pos, exp.subs, lenv, k)
        elif exp.is_a ('conditional'):
            return self.compile_conditional (tail_pos, exp, lenv, k)
        elif exp.is_a ('cexp'):
            return self.compile_primargs (exp.args, ('%cexp', exp.form, exp.type_sig), lenv, k)
        elif exp.is_a ('function'):
            return self.compile_function (tail_pos, exp, lenv, k)
        elif exp.is_a ('application'):
            return self.compile_application (tail_pos, exp, lenv, k)
        elif exp.is_a ('fix'):
            return self.compile_let_splat (tail_pos, exp, lenv, k)
        elif exp.is_a ('let_splat'):
            if self.safe_for_let_reg (tail_pos, exp, lenv, k):
                return self.compile_let_reg (tail_pos, exp, lenv, k)
            else:
                return self.compile_let_splat (tail_pos, exp, lenv, k)
        elif exp.is_a ('primapp'):
            return self.compile_primapp (tail_pos, exp, lenv, k)
        elif exp.is_a ('pvcase'):
            return self.compile_pvcase (tail_pos, exp, lenv, k)
        elif exp.is_a ('nvcase'):
            return self.compile_nvcase (tail_pos, exp, lenv, k)
        else:
            raise NotImplementedError

    def scan_constructed (self, exp):
        # add this literal to the global list
        cc = self.context.constructed

        def add (ob):
            index = len (cc)
            cc.append (ob)
            ob.index = index
            return index

        # search inside a constructed literal for other constructed literals,
        #  so we can emit them (in the correct order).
        def scan (exp):
            if exp.is_a ('primapp'):
                if exp.name == '%dtcon/symbol/t':
                    string = exp.args[0]
                    probe = self.context.symbols.get (string.value, None)
                    if probe is not None:
                        string.index, exp.index = probe
                        return exp.index
                    else:
                        index0 = add (string)
                        index1 = add (exp)
                        self.context.symbols[string.value] = (index0, index1)
                        return index1
                else:
                    for x in exp.args:
                        scan (x)
                    return None
            elif exp.is_a ('literal'):
                if exp.ltype == 'string':
                    return add (exp)
                elif exp.ltype in ('int', 'char', 'undefined'):
                    pass
                else:
                    raise ValueError ("unexpected object in constructed literal")
            else:
                raise ValueError ("huh?")

        index = scan (exp)
        if index is None:
            index = add (exp)
        return index

    # XXX a possible improvement: if we know that the body of the let
    #  makes only tail calls, then it should be safe as well.  Need to
    #  find an easy way to detect that case...

    # XXX this could be done *much* smarter.  Here's how: record the
    #   set of registers used by each and every function (transitively),
    #   which will let us know exactly which registers we can bind in
    #   a let around a call to that function.
    #
    #   For example: let's say we're about to call function X, which
    #     calls function Y.  If X uses only r0-r3, and Y only uses r0-r2,
    #     then we can safely bind to r4+.  The effect will be to let leaf-like
    #     functions use registers for binding.

    def safe_for_let_reg (self, tail_pos, exp, lenv, k):
        # we only want to use registers for bindings when
        #  1) we're in a leaf position (to avoid consuming registers
        #     too high on the stack - which means fewer registers to save
        #     around each funcall).
        #  2) there's not too many bindings (again, avoid consuming regs)
        #  3) none of the variables escape (storing a binding in a reg
        #     defeats the idea of a closure)
        if exp.leaf and len(exp.names) <= 4:
            for name in exp.names:
                if name.escapes:
                    return False
            else:
                return True
        else:
            return False

    # this optimization will mean less once we start passing arguments in registers.
    def safe_for_tr_call (self, app):
        if app.rator.is_a ('varref') and app.recursive and app.function:
            # we can only use the trcall hack when we know exactly what
            #   the stack looks like above us.  escaping funs do not provide
            #   that guarantee.
            if self.current_function.escapes:
                return False
            # XXX variables only escape if their containing function escapes,
            #   so I think this second test is redundant.
            for vardef in app.function.formals:
                if vardef.escapes:
                    return False
            return True
        else:
            return False

    def compile_application (self, tail_pos, exp, lenv, k):
        if tail_pos:
            gen_invoke = self.gen_invoke_tail
        else:
            gen_invoke = self.gen_invoke
        if tail_pos and self.safe_for_tr_call (exp):
            # special-case tail recursion to avoid consing environments
            var, addr, is_top = self.lexical_address (lenv, exp.rator.name)
            # <tr_call> needs to know how many levels of lenv to pop
            exp.depth, index = addr
            return self.compile_tr_call (exp.rands, exp, lenv, k)
        else:
            def make_application (args_reg):
                return self.compile_exp (
                    False, exp.rator, lenv, self.cont (
                        [args_reg] + k[1],
                        lambda closure_reg: gen_invoke (exp.function, closure_reg, args_reg, k)
                        )
                    )
            if len(exp.rands):
                return self.compile_rands (exp.rands, lenv, self.cont (k[1], make_application))
            else:
                return make_application (None)

    def compile_literal (self, tail_pos, exp, lenv, k):
        if exp.ltype == 'string':
            return self.gen_constructed (self.scan_constructed (exp), k)
        else:
            # immediates
            return self.gen_lit (exp, k)

    def compile_varref (self, tail_pos, exp, lenv, k):
        var, addr, is_top = self.lexical_address (lenv, exp.name)
        if addr[0] is None:
            # register variable
            return self.gen_move (addr[1], None, var.name, k)
        else:
            return self.gen_varref (addr, is_top, var, k)

    def compile_varset (self, tail_pos, exp, lenv, k):
        var, addr, is_top = self.lexical_address (lenv, exp.name)
        assert (var.name == exp.name)
        if addr[0] is None:
            # register variable
            fun = lambda reg: self.gen_move (addr[1], reg, var.name,k)
        else:
            fun = lambda reg: self.gen_assign (addr, is_top, var, reg, k)
        return self.compile_exp (False, exp.value, lenv, self.cont (k[1], fun))

    # collect_primargs is used by primops, simple_conditional, and tr_call.
    #   in order to avoid the needless consumption of registers, we re-arrange
    #   the eval order of these args - by placing the complex args first.

    def collect_primargs (self, args, regs, lenv, k, ck, reorder=True):
        args = [(args[i], i) for i in range (len (args))]
        if reorder:
            # sort args by size/complexity
            args.sort (lambda x,y: cmp (y[0].size, x[0].size))
        perm = [x[1] for x in args]
        args = [x[0] for x in args]
        #print 'collect_primargs, len(args)=', len(args)
        return self._collect_primargs (args, regs, perm, lenv, k, ck)

    def _collect_primargs (self, args, regs, perm, lenv, k, ck):
        # collect a set of arguments into registers, pass that into compiler-continuation <ck>
        if len(args) == 0:
            # undo the permutation of the args
            perm_regs = [regs[perm.index (i)] for i in range (len (perm))]
            return ck (perm_regs)
        else:
            return self.compile_exp (
                False, args[0], lenv, self.cont (
                    regs + k[1],
                    lambda reg: self._collect_primargs (args[1:], regs + [reg], perm, lenv, k, ck)
                    )
                )

    def compile_tr_call (self, args, node, lenv, k):
        return self.collect_primargs (args, [], lenv, k, lambda regs: self.gen_tr_call (node, regs))

    def compile_primargs (self, args, op, lenv, k):
        return self.collect_primargs (args, [], lenv, k, lambda regs: self.gen_primop (op, regs, k))

    def compile_primapp (self, tail_pos, exp, lenv, k):
        if exp.name.startswith ('%raccess/') or exp.name.startswith ('%rset/'):
            prim, field = exp.name.split ('/')
            # try to get constant-time field access...
            sig = itypes.get_record_sig (exp.args[0].type)
            if prim == '%raccess':
                if sig is None:
                    trace()
                return self.compile_primargs (exp.args, ('%record-get', field, sig), lenv, k)
            else:
                return self.compile_primargs (exp.args, ('%record-set', field, sig), lenv, k)                
        elif exp.name.startswith ('%rextend/'):
            return self.compile_record_literal (exp, lenv, k)
        elif exp.name.startswith ('%vector-literal/'):
            if len (exp.args) < 5:
                return self.compile_primargs (exp.args, ('%make-tuple', exp.type, 'TC_VECTOR'), lenv, k)
            else:
                return self.compile_vector_literal (exp.args, lenv, k)
        elif exp.name.startswith ('%make-vector'):
            return self.compile_primargs (exp.args, ('%make-vector',), lenv, k)
        elif exp.name.startswith ('%make-vec16'):
            return self.compile_primargs (exp.args, ('%make-vec16',), lenv, k)
        elif exp.name in ('%%array-ref', '%%product-ref'):
            # XXX need two different insns, to handle constant index
            # XXX could support strings as character arrays by passing down a hint?
            if is_a (exp.type, itypes.t_int16):
                return self.compile_primargs (exp.args, ('%vec16-ref',), lenv, k)
            else:
                return self.compile_primargs (exp.args, ('%array-ref',), lenv, k)
        elif exp.name == '%%array-set':
            if is_a (exp.args[0].type.args[0], itypes.t_int16):
                return self.compile_primargs (exp.args, ('%vec16-set',), lenv, k)
            else:
                return self.compile_primargs (exp.args, ('%array-set',), lenv, k)
        elif exp.name == '%vec16-set':
            return self.compile_primargs (exp.args, ('%vec16-set',), lenv, k)
        elif exp.name.startswith ('%vcon/'):
            ignore, label, arity = exp.name.split ('/')
            tag = self.context.variant_labels[label]
            return self.compile_primargs (exp.args, ('%make-tuple', label, tag), lenv, k)
        elif exp.name == ('&vget'):
            label, arity, index = exp.name_params
            return self.compile_primargs (exp.args, ('%vget', index), lenv, k)
        elif exp.name.startswith ('%nvget/'):
            ignore, dtype, label, index = exp.name.split ('/')
            dt = self.context.datatypes[dtype]
            if dt.uimm.has_key (label):
                return self.compile_exp (tail_pos, exp.args[0], lenv, k)
            else:
                return self.compile_primargs (exp.args, ('%vget', index), lenv, k)
        elif exp.name.startswith ('%dtcon/'):
            ignore, dtname, label = exp.name.split ('/')
            dt = self.context.datatypes[dtname]
            tag = dt.tags[label]
            if dtname == 'symbol' and exp.args[0].is_a ('literal'):
                # special case: only triggered when symbols are present in data structures
                #   that cannot be built at compile-time.
                return self.gen_constructed (self.scan_constructed (exp), k)
            elif dt.uimm.has_key (label):
                return self.compile_exp (tail_pos, exp.args[0], lenv, k)
            else:
                return self.compile_primargs (exp.args, ('%make-tuple', label, tag), lenv, k)
        elif exp.name == '%%match-error':
            return self.gen_primop (('%%match-error',), [], k)
        elif exp.name == '%%fatbar':
            # urgh, not really a primop, but rather a control feature.  I guess it should be a new node type?
            return self.compile_fatbar (tail_pos, exp.args, lenv, k)
        elif exp.name == '%%fail':
            return self.compile_fail (tail_pos, lenv, k)
        elif exp.name == '%ensure-heap':
            return self.compile_primargs (exp.args, ('%ensure-heap',), lenv, k)
        else:
            raise ValueError ("Unknown primop: %r" % (exp.name,))

    def compile_sequence (self, tail_pos, exps, lenv, k):
        if len(exps) == 0:
            raise ValueError ("illegal sequence")
        elif len(exps) == 1:
            # last expression may be in tail position
            return self.compile_exp (tail_pos, exps[0], lenv, k)
        else:
            # more than one expression
            return self.compile_exp (
                False, exps[0], lenv,
                self.dead_cont (k[1], self.compile_sequence (tail_pos, exps[1:], lenv, k))
                )

    def compile_conditional (self, tail_pos, exp, lenv, k):
        if exp.test_exp.is_a ('cexp'):
            return self.compile_simple_conditional (tail_pos, exp, lenv, k)
        else:
            return self.compile_exp (
                False, exp.test_exp, lenv, self.cont (
                    k[1],
                    lambda test_reg: self.gen_test (
                        test_reg, 
                        self.compile_exp (tail_pos, exp.then_exp, lenv, self.cont (k[1], lambda reg: self.gen_jump (reg, k))),
                        self.compile_exp (tail_pos, exp.else_exp, lenv, self.cont (k[1], lambda reg: self.gen_jump (reg, k))),
                        k
                        )
                    )
                )

    def compile_simple_conditional (self, tail_pos, exp, lenv, k):
        def finish (regs):
            return self.gen_simple_test (
                exp.test_exp.params,
                regs,
                self.compile_exp (tail_pos, exp.then_exp, lenv, self.cont (k[1], lambda reg: self.gen_jump (reg, k))),
                self.compile_exp (tail_pos, exp.else_exp, lenv, self.cont (k[1], lambda reg: self.gen_jump (reg, k))),
                k
                )
        return self.collect_primargs (exp.test_exp.args, [], lenv, k, finish)

    def compile_pvcase (self, tail_pos, exp, lenv, k):
        def finish (test_reg):
            jump_k = self.cont (k[1], lambda reg: self.gen_jump (reg, k))
            alts = [self.compile_exp (tail_pos, alt, lenv, jump_k) for alt in exp.alts]
            return self.gen_pvcase (test_reg, exp.alt_formals, alts, k)
        return self.compile_exp (False, exp.value, lenv, self.cont (k[1], finish))

    def compile_nvcase (self, tail_pos, exp, lenv, k):
        dt = self.context.datatypes[exp.vtype]
        def finish (test_reg):
            jump_k = self.cont (k[1], lambda reg: self.gen_jump (reg, k))
            alts = [self.compile_exp (tail_pos, alt, lenv, jump_k) for alt in exp.alts]
            ealt = self.compile_exp (tail_pos, exp.else_clause, lenv, jump_k)
            if len(dt.alts) != len(alts) and ealt.name == 'primop' and ealt.params[0] == '%%match-error':
                raise IncompleteMatch (exp)
            return self.gen_nvcase (test_reg, exp.vtype, exp.tags, alts, ealt, k)
        return self.compile_exp (False, exp.value, lenv, self.cont (k[1], finish))

    fatbar_counter = 0
    def compile_fatbar (self, tail_pos, (e1, e2), lenv, k):
        label = 'fatbar_%d' % (self.fatbar_counter,)
        lenv0 = (fatbar_rib (label), lenv)
        self.fatbar_counter += 1
        return self.gen_fatbar (
            label,
            self.compile_exp (tail_pos, e1, lenv0, self.cont (k[1], lambda reg: self.gen_jump (reg, k))),
            self.compile_exp (tail_pos, e2, lenv,  self.cont (k[1], lambda reg: self.gen_jump (reg, k))),
            k
            )

    def compile_fail (self, tail_pos, lenv, k):
        # lookup the closest surrounding fatbar label
        search = lenv
        # lexical depth to pop off
        d = 0
        while search:
            rib, search = search
            if is_a (rib, fatbar_rib):
                return self.gen_fail (d, rib.name, k)
            elif is_a (rib, register_rib):
                # ignore
                pass
            else:
                d += 1
        else:
            raise ValueError ("%%fail without fatbar??")

    def compile_function (self, tail_pos, exp, lenv, k):
        self.current_function = exp
        if len(exp.formals):
            # don't extend the environment if there are no args
            lenv = (exp.formals, lenv)
        return self.gen_closure (
            exp,
            self.compile_exp (True, exp.body, lenv, self.cont ([], self.gen_return)),
            k
            )

    def compile_let_splat (self, tail_pos, exp, lenv, k):
        if len (exp.inits) == 0:
            # no bindings, just compile the body
            return self.compile_exp (tail_pos, exp.body, lenv, k)
        # becomes this sequence:
        #   (new_env, push_env, store_env0, ..., <body>, pop_env)
        k_body = self.dead_cont (k[1], self.compile_exp (tail_pos, exp.body, (exp.names, lenv),
                                                         self.cont (k[1], lambda reg: self.gen_pop_env (reg, k))))
        return self.gen_new_env (
            len (exp.names),
            self.cont (
                k[1],
                lambda tuple_reg: self.gen_push_env (
                    tuple_reg,
                    self.dead_cont (
                        k[1],
                        self.compile_store_rands (
                            0, 1, exp.inits, tuple_reg,
                            [tuple_reg] + k[1],
                            (exp.names, lenv),
                            k_body)
                        )
                    )
                )
            )

    def compile_let_reg (self, tail_pos, exp, lenv, k):

        # since this is a let-*splat*, we're forced to compile this one variable at a time,
        #   which makes the register 'rib' look a little silly.  XXX redo it as 'register_var'.

        def loop (names, inits, lenv, regs):
            if len(inits) == 0:
                return self.compile_exp (tail_pos, exp.body, lenv, (k[0], k[1] + regs, k[2]))
            else:
                return self.compile_exp (
                    False, inits[0], lenv, self.cont (
                        regs + k[1],
                        lambda reg: loop (
                            names[1:],
                            inits[1:],
                            (register_rib ([names[0]], [reg]), lenv),
                            regs + [reg]
                            )
                        )
                    )

        return loop (exp.names, exp.inits, lenv, [])

    opt_collect_args_in_regs = False

    if opt_collect_args_in_regs:
        # simply collect the args into registers, then use a <build_env> insn to populate the rib.
        # Note that collect_primargs will re-order the args...
        def compile_rands (self, rands, lenv, k):
            return self.collect_primargs (rands, [], lenv, k, lambda regs: self.gen_build_env (regs, k))
    else:
        # allocate the env rib, then place each arg in turn.
        # NOTE:
        #   to change the order of evaluation to right-to-left, you need to:
        #   1) pass i+1 to compile_tuple_rands
        #   2) make "i>0" the test, and
        #   3) i-1 the iter
        # then beware of callers expecting the other behavior (like let*)
        def compile_rands (self, rands, lenv, k):
            if not rands:
                return self.gen_new_env (0, k)
            else:
                return self.gen_new_env (
                    len (rands),
                    self.cont (k[1], lambda tuple_reg: self.compile_store_rands (0, 1, rands, tuple_reg, [tuple_reg] + k[1], lenv, k))
                    )

    # if we use collect_primargs() to populate literal vectors and records, the code
    #   emitted consumes one register for each arg before finally storing all the registers
    #   in one pass.  As the literals become larger, the register usage becomes very wasteful.
    # instead, this function accumulates the args one at a time, and stores them individually
    #   into the tuple.
        
    def compile_store_rands (self, i, offset, rands, tuple_reg, free_regs, lenv, k):
        # offset is an additional offset from the beginning of the tuple - used only
        #  when storing into environment ribs (because of the <next> pointer immediately
        #  after the tag).
        return self.compile_exp (
            False, rands[i], lenv, self.cont (
                free_regs,
                lambda arg_reg: self.gen_store_tuple (
                    offset, arg_reg, tuple_reg, i, len(rands),
                    (self.dead_cont (free_regs, self.compile_store_rands (i+1, offset, rands, tuple_reg, free_regs, lenv, k)) if i+1 < len(rands) else k)
                    )
                )
            )

    def compile_vector_literal (self, rands, lenv, k):
        return self.gen_new_tuple (
            'TC_VECTOR', len (rands),
            self.cont (k[1], lambda vec_reg: self.compile_store_rands (0, 0, rands, vec_reg, [vec_reg] + k[1], lenv, k))
            )

    def get_record_tag (self, sig):
        #print 'get record tag', sig
        c = self.context
        if not c.records2.has_key (sig):
            c.records2[sig] = len (c.records2)
            for label in sig:
                if not c.labels2.has_key (label):
                    c.labels2[label] = len (c.labels2)
        return c.records2[sig]

    def compile_record_literal (self, exp, lenv, k):
        # unwind row primops into a record literal
        # (%rextend/field0 (%rextend/field1 (%rmake) ...)) => {field0=x field1=y}
        fields = []
        while 1:
            if exp.is_a ('primapp') and exp.name == '%rmake':
                # we're done...
                break
            elif exp.is_a ('primapp') and exp.name.startswith ('%rextend/'):
                ignore, field = exp.name.split ('/')
                fields.append ((field, exp.args[1]))
                exp = exp.args[0]
            else:
                return self.compile_record_extension (fields, exp, lenv, k)
        # put the names into canonical order (sorted by label)
        fields.sort (lambda a,b: cmp (a[0],b[0]))
        # lookup the runtime tag for this record
        sig = tuple ([x[0] for x in fields])
        tag = 'TC_USEROBJ+%d' % (self.get_record_tag (sig) << 2)
        # now compile the expression as a %make-tuple
        args = [x[1] for x in fields]
        return self.gen_new_tuple (
            tag, len (args),
            self.cont (k[1], lambda rec_reg: self.compile_store_rands (0, 0, args, rec_reg, [rec_reg] + k[1], lenv, k))
            )

    def compile_record_extension (self, fields, exp, lenv, k):
        # ok, we have a source record {a,b} to which we want to add
        #   one or more fields {c,d}.  We'll need to compile a
        #   'make-tuple' with args fetched from the source record
        #   mixed in with new args, all in the correct order.
        sig = itypes.get_record_sig (exp.type)
        if '...' in sig:
            raise ValueError ("can't extend record - only a partial type available")
        labels = [x[0] for x in fields]
        labels.sort()
        args = [x[1] for x in fields]
        new_sig = list(set(sig).union (set(labels)))
        new_sig.sort()
        new_sig = tuple (new_sig)
        if sig == new_sig:
            # identical, it's actually an update
            # XXX should consider doing copy+update instead, for functional cred.
            # XXX another option: consider it an error.
            # the last sounds best: principle of least surprise.
            assert (len(fields) == 1)
            return self.compile_primargs ([exp, args[0]], ('%record-set', fields[0][0], sig), lenv, k)
        else:
            new_tag = self.get_record_tag (new_sig)
            return self.compile_primargs ([exp] + args, ('%extend-tuple', labels, sig, new_tag), lenv, k)

    # --- continuations ---

    def cont (self, free_regs, generator):
        # allocate a register for this continuation, then generate the
        #   code that will create the value to go into it.
        reg = self.regalloc.allocate (free_regs)
        return (reg, free_regs, generator (reg))

    def dead_cont (self, free_regs, k):
        # a 'dead' continuation - only for a side-effect.  Doesn't need a register allocated.
        return ('dead', free_regs, k)

class register_allocator:

    def __init__ (self):
        self.max_reg = -1

    def allocate (self, free_regs):
        i = 0
        while 1:
            if i not in free_regs:
                self.max_reg = max (self.max_reg, i)
                return i
            else:
                i += 1
        
def box (n):
    return (n<<1)|1

class INSN:

    allocates = 0

    def __init__ (self, name, regs, params, k):
        self.name = name
        self.regs = regs
        self.params = params
        self.k = k
        self.subs = ()

    def print_info (self):
        if self.name == 'test':
            return '%s %r %r' % (self.name, self.regs, self.params[0])
        elif self.name == 'close':
            return '%s %r %r' % (self.name, self.regs, self.params[0].name)
        elif self.name in ('pvcase', 'nvcase'):
            return '%s %r %r' % (self.name, self.params[0], self.regs)
        elif self.name == 'fatbar':
            return '%s %r %r' % (self.name, self.params[0], self.regs)
        else:
            return '%s %r %r %r' % (self.name, self.free_regs, self.regs, self.params)

    def __repr__ (self):
        return '<INSN %s>' % (self.print_info())

class cps (compiler):

    """generates 'register' CPS"""


    def gen_lit (self, lit, k):
        # these smarts probably belong in the back end.
        if lit.ltype == 'int':
            return INSN ('lit', [], box (lit.value), k)
        elif lit.ltype == 'bool':
            if lit.value == 'true':
                n = 0x106
            else:
                n = 0x6
            return INSN ('lit', [], n, k)
        elif lit.ltype == 'char':
            if lit.value == 'eof':
                # special case
                val = 257<<8|0x02
            else:
                val = ord(lit.value)<<8|0x02
            return INSN ('lit', [], val, k)
        elif lit.ltype == 'undefined':
            return INSN ('lit', [], 0x0e, k)
        elif lit.ltype == 'nil':
            return INSN ('lit', [], 0x0a, k)
        else:
            raise SyntaxError

    def gen_constructed (self, exp, k):
        return INSN ('constructed', [], exp, k)

    def gen_primop (self, primop, regs, k):
        return INSN ('primop', regs, primop, k)

    def gen_move (self, reg_var, reg_src, name, k):
        return INSN ('move', [reg_var, reg_src], name, k)

    def gen_jump (self, reg, k):
        # k[0] is the target for the whole conditional
        return INSN ('jump', [reg, k[0]], None, None)

    def gen_fatbar (self, label, e1, e2, k):
        return INSN ('fatbar', [], (label, e1, e2), k)

    def gen_fail (self, depth, label, k):
        return INSN ('fail', [], (label, depth), None)

    def gen_new_env (self, size, k):
        return INSN ('new_env', [], size, k)

    def gen_build_env (self, regs, k):
        return INSN ('build_env', regs, None, k)

    def gen_push_env (self, reg, k):
        return INSN ('push_env', [reg], None, k)

    def gen_pop_env (self, reg, k):
        return INSN ('pop_env', [reg], None, k)

    def gen_new_tuple (self, tag, size, k):
        return INSN ('new_tuple', [], (tag, size), k)

    def gen_store_tuple (self, offset, arg_reg, tuple_reg, i, n, k):
        return INSN ('store_tuple', [arg_reg, tuple_reg], (i, offset, n), k)

    def gen_varref (self, addr, is_top, var, k):
        return INSN ('varref', [], (addr, is_top, var), k)
    
    def gen_assign (self, addr, is_top, var, reg, k):
        return INSN ('varset', [reg], (addr, is_top, var), k)

    def gen_closure (self, fun, body, k):
        # track all functions for the back end
        self.context.functions.append (fun)
        return INSN ('close', [], (fun, body, k[1]), k)

    def gen_test (self, test_reg, then_code, else_code, k):
        return INSN ('test', [test_reg], (None, then_code, else_code), k)

    def gen_simple_test (self, cexp, regs, then_code, else_code, k):
        return INSN ('test', regs, (cexp, then_code, else_code), k)

    def gen_pvcase (self, test_reg, types, alts, k):
        return INSN ('pvcase', [test_reg], (types, alts), k)

    def gen_nvcase (self, test_reg, dtype, tags, alts, ealt, k):
        return INSN ('nvcase', [test_reg], (dtype, tags, alts, ealt), k)

    def gen_invoke_tail (self, fun, closure_reg, args_reg, k):
        return INSN ('invoke_tail', [closure_reg, args_reg], fun, None)

    def gen_invoke (self, fun, closure_reg, args_reg, k):
        return INSN ('invoke', [closure_reg, args_reg], (k[1], fun), k)

    def gen_tr_call (self, app_node, regs):
        return INSN ('tr_call', regs, (app_node.depth, app_node.function), None)

    def gen_return (self, val_reg):
        return INSN ('return', [val_reg], None, None)

    def go (self, exp):
        lenv = None
        # only enable the 'top lenv' hack if the top level is a fix
        self.use_top = exp.is_a ('fix')
        result = self.compile_exp (True, exp, lenv, self.cont ([], self.gen_return))
        result = flatten (result)
        #pretty_print (result)
        #remove_moves (result)
        find_allocation (result, self.verbose)
        return result

def flatten (exp):
    r = []
    while exp:
        #print exp
        if exp.k:
            target, free_regs, next = exp.k
        else:
            next = None
            target = None
            free_regs = []
        exp.k = None
        exp.target = target
        exp.free_regs = free_regs
        if exp.name == 'test':
            name, then_code, else_code = exp.params
            exp.params = name, flatten (then_code), flatten (else_code)
        elif exp.name == 'close':
            node, body, free = exp.params
            exp.params = node, flatten (body), free
        elif exp.name == 'pvcase':
            types, alts = exp.params
            exp.params = types, [flatten (x) for x in alts]
        elif exp.name == 'nvcase':
            types, tags, alts, ealt = exp.params
            exp.params = types, tags, [flatten (x) for x in alts], flatten (ealt)
        elif exp.name == 'fatbar':
            label, e1, e2 = exp.params
            exp.params = label, flatten (e1), flatten (e2)
        r.append (exp)
        exp = next
    return r

import sys
W = sys.stdout.write

def pretty_print (insns, depth=0): 
   for insn in insns:
        W ('%s' % ('  ' * depth))
        if insn.target == 'dead':
            W ('   -   ')
        elif insn.target is None:
            W ('       ')
        else:
            W ('%4d = ' % (insn.target,))
        W ('%s\n' % (insn.print_info(),))
        # special case prints
        if insn.name == 'test':
            name, then_code, else_code = insn.params
            pretty_print (then_code, depth+1)
            pretty_print (else_code, depth+1)
        elif insn.name == 'close':
            node, body, free = insn.params
            pretty_print (body, depth+1)
        elif insn.name == 'pvcase':
            types, alts = insn.params
            for alt in alts:
                pretty_print (alt, depth+1)
        elif insn.name == 'nvcase':
            types, tags, alts, ealt = insn.params
            for alt in alts + [ealt]:
                pretty_print (alt, depth+1)
        elif insn.name == 'fatbar':
            label, e1, e2 = insn.params
            pretty_print (e1, depth+1)
            pretty_print (e2, depth+1)

# when <let> expressions are in a leaf position, the bindings may be
#  be stored in registers rather than an environment tuple.  due to 
#  the way the CPS algorithm works, there are a lot of redundant move
#  insns generated that we can ignore by remapping the relevant registers.                

# Ok, this doesn't work correctly [yet].  The problem comes up when varset
#   causes regs to get remapped - tests/t_bad_inline.scm fails.

def remove_moves (insns):
    map = {}
    for insn in insns:
        name = insn.name
        if insn.name == 'move':
            # a new entry in map
            src = insn.regs[0]
            # note: <src> may already be in the map!
            while map.has_key (src):
                # follow the chain of references
                src = map[src]
            # src == target sometimes happens, don't go all infinite loop.
            if insn.target != 'dead' and insn.target != src:
                print 'map %d == %d' % (insn.target, src)
                map[insn.target] = src
        # rename any that we can
        insn.regs = [ map.get(x,x) for x in insn.regs ]
        # special case
        if insn.name == 'test':
            name, then_code, else_code = insn.params
            remove_moves (then_code)
            remove_moves (else_code)
        elif insn.name == 'close':
            node, body, free = insn.params
            remove_moves (body)
        elif insn.name in ('pvcase', 'nvcase'):
            types, alts = insn.params
            for alt in alts:
                remove_moves (alt)
        elif insn.name == 'fatbar':
            # XXX never tested, dead code
            label, e1, e2 = insn.params
            remove_moves (e1)
            remove_moves (e2)
        if insn.name != 'move' and map.has_key (insn.target):
            # remove any that are blown away
            del map[insn.target]

def walk (insns):
    "iterate the entire tree of insns"
    for insn in insns:
        yield (insn)
        if insn.name == 'test':
            name, then_code, else_code = insn.params
            for x in walk (then_code):
                yield x
            for x in walk (else_code):
                yield x
        elif insn.name == 'close':
            node, body, free = insn.params
            for x in walk (body):
                yield x
        elif insn.name == 'pvcase':
            types, alts = insn.params
            for alt in alts:
                for y in walk (alt):
                    yield y
        elif insn.name == 'nvcase':
            types, tags, alts, ealt = insn.params
            for alt in alts + [ealt]:
                for y in walk (alt):
                    yield y
        elif insn.name == 'fatbar':
            label, e1, e2 = insn.params
            for x in walk (e1):
                yield x
            for x in walk (e2):
                yield x

def walk_function (insns):
    "iterate only the insns in this function body"
    for insn in insns:
        yield (insn)
        if insn.name == 'test':
            name, then_code, else_code = insn.params
            for x in walk_function (then_code):
                yield x
            for x in walk_function (else_code):
                yield x
        elif insn.name == 'nvcase':
            types, tags, alts, ealt = insn.params
            for alt in alts + [ealt]:
                for x in walk_function (alt):
                    yield x
        elif insn.name == 'pvcase':
            types, alts = insn.params
            for alt in alts:
                for x in walk_function (alt):
                    yield x
        elif insn.name == 'fatbar':
            label, e1, e2 = insn.params
            for x in walk_function (e1):
                yield x
            for x in walk_function (e2):
                yield x

def find_allocation (insns, verbose):
    funs = [ x for x in walk (insns) if x.name == 'close' ]
    # examine each fun to see if it performs allocation
    for fun in funs:
        node, body, free = fun.params
        fun.allocates = 0
        for insn in walk_function (body):
            if insn.name == 'primop':
                if insn.params[0] == '%make-tuple' and len(insn.regs):
                    # we're looking for non-immediate constructors (i.e., list/cons but not list/nil)
                    fun.allocates += 1
                elif insn.params[0] in ('%make-vector', '%extend-tuple'):
                    fun.allocates += 1
            elif insn.name in ('new_env', 'build_env', 'new_tuple', 'invoke', 'close', 'make_string'):
                fun.allocates += 1
        if verbose:
            print 'allocates %d %s' % (fun.allocates, fun.params[0].name)