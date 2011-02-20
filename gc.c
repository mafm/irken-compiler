
// --------------------------------------------------
// cheney copying garbage collector
// --------------------------------------------------

object
do_gc (int nroots)
{
  object * scan;
  int i = 0;

  inline int sitting_duck (object * p) {
    return (p >= heap0) && (p < (heap0 + heap_size));
  }

  object * copy (object * p) {
    object * pp = (object *) *p;
    if (is_immediate (pp)) {
      return pp;
    } else if (sitting_duck (pp)) {
      if (*pp == (object) GC_SENTINEL) {
        // pp points into to_space, return the forwarding address
        return (object *) (*(pp+1));
      } else {
	// p points at an object in from_space, copy it
	object * addr = freep;
	pxll_int length = GET_TUPLE_LENGTH (*pp);
	pxll_int k;
	// copy tag, children
	for (k=0; k < length+1; k++) {
	  *freep++ = *pp++;
	}
	// leave a sentinel where the tag was, followed by the forwarding address.
	*(object*)(*p) = (object) GC_SENTINEL;
	*((object*)(*p)+1) = (object) addr;
	return addr;
      }
    } else {
      // pp points outside of the heap
      //fprintf (stderr, "?");
      return pp;
    }
  }

  if (verbose_gc) {
    fprintf (stderr, "[gc...");
  }

  // place our roots
  scan = heap1;
  freep = scan + nroots;

  // copy the roots
  for (i = 0; i < nroots; i++) {
    scan[i] = (object) copy (&(scan[i]));
  }
      
  // bump scan
  scan += nroots;

  // scan loop
      
  while (scan < freep) {
    if (IMMEDIATE (*scan)) {
      scan++;
    } else {
      object * p = scan + 1;
      unsigned char tc = GET_TYPECODE (*scan);
      pxll_int length = GET_TUPLE_LENGTH (*scan);
      pxll_int i;

      switch (tc) {

      case TC_CLOSURE:
	// closure = { tag, pc, lenv }
	p++;			// skip pc
	*p = copy (p); p++;	// lenv
	scan += 3;
	break;

      case TC_SAVE:
	// save = { tag, next, lenv, pc, regs[...] }
	*p = copy (p); p++;		// next
	*p = copy (p); p++;		// lenv
	p++;				// pc
	for (i=3; i < length; i++) {
	  *p = copy (p);
	  p++;
	}
	scan += length + 1;
	break;

      case TC_STRING:
      case TC_VEC16:
	// skip it all
	scan += length + 1;
	break;

      default:
        // copy everything
        for (i=0; i < length; i++) {
          *p = copy (p); p++;
        }
        scan += length + 1;
        break;
      }
    }
  }
  // swap heaps
  { object * temp = heap0; heap0 = heap1; heap1 = temp; }

  if (clear_fromspace) {
    // zero the from-space
    clear_space (heap1, heap_size);
  }

  if (clear_tospace) {
    clear_space (freep, heap_size - (freep - heap0));
  }

  if (verbose_gc) {
    fprintf (stderr, "collected %" PRIuPTR " words]\n", freep - heap0);
  }
  return (object) box (freep - heap0);
}

object
gc_flip (int nregs)
{
  object nwords;
  // copy roots
  heap1[0] = (object) lenv;
  heap1[1] = (object) k;
  heap1[2] = (object) top;
  //assert (freep < (heap0 + heap_size));
  gc_regs_in (nregs);
  nwords = do_gc (nregs + 3);
  // replace roots
  lenv = (object *) heap0[0];
  k    = (object *) heap0[1];
  top  = (object *) heap0[2];
  gc_regs_out (nregs);
  // set new limit
  limit = heap0 + (heap_size - 1024);
  return nwords;
}

// exactly the same, except <thunk> is an extra root.
// Warning: dump_image() knows how many roots are used here.
object *
gc_dump (object * thunk)
{
  // copy roots
  heap1[0] = (object) lenv;
  heap1[1] = (object) k;
  heap1[2] = (object) top;
  heap1[3] = (object) thunk;
  do_gc (4);
  // replace roots
  lenv  = (object *) heap0[0];
  k     = (object *) heap0[1];
  top   = (object *) heap0[2];
  thunk = (object *) heap0[3];
  // set new limit
  limit = heap0 + (heap_size - 1024);
  return thunk;
}


void
gc_relocate (int nroots, object * start, object * finish, pxll_int delta)
{
  void adjust (object * q) {
    if ((*q) && (!IMMEDIATE(*(q)))) {
      // get the pointer arith right
      object ** qq = (object **) q;
      *(qq) -= delta;
    }
  }
  object * scan = start;
  int i;

  // roots are either immediate values, or pointers to tuples
  // (map adjust roots)

  for (i=0; i < nroots; i++, scan++) {
    adjust (scan);
  }

  while (scan < finish) {
    // There must be a tuple here
    int tc = GET_TYPECODE (*scan);
    pxll_int length = GET_TUPLE_LENGTH (*scan);
    object * p = scan + 1;
    int i;

    switch (tc) {

      // XXX if we moved SAVE's <pc> to the front, then these two could be identical...
    case TC_CLOSURE:
      // { tag, pc, lenv }
      p++; // skip pc (XXX: actually, pc will have its own adjustment)
      adjust (p);
      scan += length+1;
      break;
    case TC_SAVE:
      // { tag, next, lenv, pc, regs[...] }
      adjust (p); p++;
      adjust (p); p++;
      p++; // skip pc (XXX: ...)
      scan += length+1;
      break;
    case TC_STRING:
    case TC_VEC16:
      // skip it all
      scan += length+1;
      break;
    default:
      // adjust everything
      for (i=0; i < length; i++, p++) {
        adjust (p);
      }
      scan += length+1;
      break;
    }
  }
}
