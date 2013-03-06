import logging
import re
from mock import Mock

log = logging.getLogger(__name__)

class ProgrammableMock():
    """
    Erwan's own little expectation mock library.
    Similar to mox and fudge.

    A programmable mock helps you mock a class whose methods should be
    called in a given order and return varying results depending on when they
    are called. It will assert that methods are called in the specified order,
    with the expected arguments, that no unexpected method is being called, and
    that none of the expected calls remains uncalled when the unittest
    completes.

    A ProgrammableMock is typically used as follows:

    # Create a mock of the class CloudStack.Client, with all methods
    # of the CloudStack.Client raising an exception by default.
    pmock = ProgrammableMock('CloudStack.Client')

    # Then tell which calls to expect, with which arguments and what to return:
    pmock.expect('listProjects',        # Name of 1st method call expected
              ({'listall': 'true'},),   # Which arguments are expected
              ({'name':'projectA', 'id': 1},{'name':'projectB', 'id': 2},)) # What to return
    pmock.expect('listProjectAccounts',
              ({'projectid': 1},),
              ({'id': 1, 'account': 'username1', 'accounttype': 0}, {'id': 2, 'account': getpass.getuser(), 'accounttype': 0},))

    # Inject the mock where relevant
    def _get_client():
        return pmock.get_mock()
    utils.cloudstack.get_client = _get_client

    # Then do some unittests
    do_tests()

    # Then assert that all the mocked methods were called as expected
    pmock.assert_done()
    """


    # self.stack is a stack of ('methodname', (args), (results)) describing
    # each method call expected, in the expected order, and what it should
    # return.
    def __init__(self, mockclass):
        """Instantiate a Mock object"""
        self.mock = Mock()
        self.stack = []
        self.mockclass = mockclass

        # Generate a default exception side_effect for all methods in mockclass
        attr = {}

        # Dynamically load module and class, then lookup its public methods
        components = mockclass.split('.')
        module = __import__(mockclass)
        components.pop(0)
        for comp in components:
            module = getattr(module, comp)

        for methodname in dir(module):
            if methodname[:2] != '__':

                # Make sure mockclass and methodname have values tied to the scope of _default_call
                def callback_factory(mockclass, methodname):
                    def _default_call(*args, **kwargs):
                        raise Exception("ProgrammableMock of %s got unhandled call to %s with arguments %s" % (mockclass, methodname, args))
                    return _default_call

                attr['%s.side_effect' % methodname] = callback_factory(mockclass, methodname)
        self.mock.configure_mock(**attr)

    def get_mock(self):
        return self.mock

    def reset(self):
        self.stack = []
        self.mock.reset_mock()

    def _pop_method_stack(self, method):
        def _assert_call(*args, **kwargs):
            log.debug("Mock of %s got call to %s with args %s" % (self.mockclass, method, args))
            if len(self.stack) == 0:
                raise Exception("ProgrammableMock of %s intercepted unexpected call to %s with arguments %s" % (self.mockclass, method, args))
            (expectmethod, expectargs, results) = self.stack.pop(0)
            # TODO: if expectargs is a def, call it and let it assert
            assert method == expectmethod, "expected a call to %s, got one to %s with arguments %s" % (expectmethod, method, args)
            if expectargs != 'IGNORE':
                assert args == expectargs, "expected a call to %s with args %s, but got %s" % (method, expectargs, args)
            return results
        return _assert_call

    def expect(self, method, args, results):
        self.stack.append((method, args, results))
        attr = {'%s.side_effect' % method: self._pop_method_stack(method)}
        log.debug("Stacking expected call to %s.%s" % (self.mockclass, method))
        self.mock.configure_mock(**attr)

    def assert_done(self):
        assert len(self.stack) == 0, "Expected the ProgrammableMock stack to be empty, but contained: %s" % self.stack
