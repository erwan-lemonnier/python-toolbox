# see:
# http://www.python.org/dev/peps/pep-0343/
# http://docs.python.org/2/library/contextlib.html

import sys
import contextlib

@contextlib.contextmanager
def yield_something(fail=False):
    # Return only once (all after yield is the *finalize* part)
    print "doing setup"
    if fail:
        raise Exception("yield_something failed!")
    yield "value"
    print "doing teardown"

@contextlib.contextmanager
def yield_something2(fail=False):
    # Same, but handle exceptions in the calling block
    print "doing setup"
    try:
        if fail:
            raise Exception("yield_something2 failed!")
        yield "value"
    except Exception, e:
        print "yield_something2 caught: %s" % e
    finally:
        print "doing teardown"

def example():
    # this one just goes through
    print "EXAMPLE 1: contextmanager returns 1 value"
    with yield_something() as foo:
        print "processing yielded %s" % foo

    # this one fails and does not handle exception
    try:
        print "EXAMPLE 2: context manager raises an unhandled exception"
        with yield_something(fail=True) as foo:
            print "processing yielded %s" % foo
    except Exception, e:
        print "yield_something failed: %s (teardown not executed)" % e

    # this one fails in the with block
    print "EXAMPLE 3: with block raises an exception, caught by the contextmanager"
    with yield_something2() as foo:
        raise Exception("with block raises exception")
        print "processing yielded %s" % foo

    # this one fails while in yield_something2(), before it yields
    print "EXAMPLE 4: the contextmanager fails before yielding"
    with yield_something2(fail=True) as foo:
        print "processing yielded %s" % foo

if __name__ == "__main__":
    sys.exit(example())
