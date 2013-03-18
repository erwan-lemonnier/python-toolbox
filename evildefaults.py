# An example of how python stores default
# values and how their state can be modified
# in unexpected ways...

def foo(l=[]):
    l.append("a")
    return l

print "calling foo(l=['b']): "
print foo(l=["b"])
print "defaults: "
print foo.__defaults__
print "calling foo(): "
print foo()
print "defaults: "
print foo.__defaults__
print "calling foo(): "
print foo()
print "defaults: "
print foo.__defaults__
