# -*- Mode: Python; coding: utf-8 -*-

is_a = isinstance

# 'itypes' since 'types' is a standard python module

class _type:
    pass

class t_base (_type):
    name = 'base'
    def __cmp__ (self, other):
        return cmp (self.__class__, other.__class__)
    def __repr__ (self):
        return self.name
    def __hash__ (self):
        return hash (self.name)

class t_int (t_base):
    name = 'int'

class t_char (t_base):
    name = 'char'

class t_string (t_base):
    name = 'string'

# XXX consider using a true/false variant, then implementing 'if' as a filter.
class t_bool (t_base):
    name = 'bool'

class t_undefined (t_base):
    name = 'undefined'

# XXX may use product() instead...
class t_unit (t_base):
    name = 'unit'

base_types = {
    'int' : t_int(),
    'bool' : t_bool(),
    'char' : t_char(),
    'string' : t_string(),
    'undefined' : t_undefined(),
    'unit': t_unit(),
    }

def base_n (n, base, digits):
    # return a string representation of <n> in <base>, using <digits>
    s = []
    while 1:
        n, r = divmod (n, base)
        s.insert (0, digits[r])
        if not n:
            break
    return ''.join (s)

class t_var (_type):
    next = None
    rank = -1
    letters = 'abcdefghijklmnopqrstuvwxyz'
    eq = None
    counter = 0
    def __init__ (self):
        self.id = t_var.counter
        t_var.counter += 1
    def __repr__ (self):
        return base_n (self.id, len(self.letters), self.letters)

class t_predicate (_type):
    def __init__ (self, name, args):
        self.name = name
        self.args = tuple (args)
    def __repr__ (self):
        # special case
        if self.name == 'arrow':
            if len(self.args) == 2:
                return '%r->%r' % (self.args[1], self.args[0])
            else:
                return '%r->%r' % (self.args[1:], self.args[0])
        else:
            return '%s%r' % (self.name, self.args)

def is_pred (t, *p):
    # is this a predicate from the set <p>?
    return is_a (t, t_predicate) and t.name in p

def arrow (*sig):
    # sig = (<result_type>, <arg0_type>, <arg1_type>, ...)
    # XXX this might be more clear as (<arg0>, <arg1>, ... <result>)
    return t_predicate ('arrow', sig)
    
# row types
def product (*args):
    # a.k.a. 'Π'
    return t_predicate ('product', args)

def sum (row):
    # a.k.a. 'Σ'
    return t_predicate ('sum', (row,))

def rdefault (arg):
    # a.k.a. 'δ'
    return t_predicate ('rdefault', (arg,))

def rlabel (name, type, rest):
    return t_predicate ('rlabel', (name, type, rest))

def abs():
    return t_predicate ('abs', ())

def pre (x):
    return t_predicate ('pre', (x,))

def parse_cexp_type (t):
    if is_a (t, tuple):
        result_type, arg_types = t
        return arrow (parse_cexp_type (result_type), *[parse_cexp_type (x) for x in arg_types])
    elif is_a (t, str):
        return base_types[t]
    else:
        raise ValueError (t)
    
def get_record_sig (t):
    # product (rlabel (...))
    assert (is_pred (t, 'product'))
    labels = []
    t = t.args[0]
    while 1:
        if is_pred (t, 'rlabel'):
            label, type, rest = t.args
            if is_pred (type, 'pre'):
                labels.append (label)
            t = rest
        elif is_pred (t, 'rdefault'):
            break
        else:
            return None
    labels.sort()
    return tuple (labels)
