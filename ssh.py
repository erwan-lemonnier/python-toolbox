#!/usr/bin/env python

import subprocess
import select
import logging
import re
import time
from types import ListType, StringType, IntType


class SshException(Exception):
    """Any exception happening while using ssh to execute remote commands"""
    pass

class SshCmdTimeoutException(SshException):
    """A timeout occured while waiting for a command to complete"""
    pass

class SshCmdFailedException(SshException):
    """A command executed in the remote shell returned a non-zero return code"""
    pass

class SshTunnelPool(object):
    """A pool of ssh tunnels

    The pool of tunnels is a singleton, ensuring that it does not get
    garbage collected together with instances of Ssh.
    """

    verbose = False

    @staticmethod
    def be_verbose():
        """Make all new tunnels in the pool use 'ssh -vvv' to see ssh debug messages"""
        SshTunnelPool.verbose = True

    def __init__(self):
        self.tunnels = dict()

    def get_tunnel(self, hostname, daemon=None, ssh_options=None):
        """Return a tunnel to the given hostname, running a given background command.

        'hostname' is the name of the host on which the remote sshd is running.

        'daemon' is an optional array of shell commands that should start a
        process that listens for commands on stdin and gives replies on
        stdout/stderr. If no 'daemon' is given, the ssh user's shell is used.
        """

        key = "%s-%s" % (hostname, daemon)

        if key in self.tunnels:
            logging.debug("Tunnel to %s is already open. Returning it." % key)
            return self.tunnels[key]

        logging.debug("Opening ssh tunnel to %s" % key)

        opts = ['ssh', '-x', '-T', '-oBatchMode=yes']

        if SshTunnelPool.verbose:
            opts.append('-vvv')

        # Add eventual ssh options
        if ssh_options:
            opts = opts + ssh_options

        opts.append(hostname)

        # Running an eventual background command
        if daemon:
            opts = opts + daemon

        logging.debug("Registered tunnel [%s]" % ' '.join(opts))

        # Store tunnel for later usage
        tunnel = subprocess.Popen(opts, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.tunnels[key] = tunnel
        return tunnel

    def kill_tunnel(self, hostname, daemon=None):
        """Close a pooled ssh tunnel (identified by its hostname and optional
        daemon commands) and kill anything it was running on the remote side.
        Note that killing works only for Python version >= 2.6
        """

        key = "%s-%s" % (hostname, daemon)

        if key in self.tunnels:
            logging.debug("Killing ssh tunnel to %s" % key)
            tunnel = self.tunnels[key]
            try:
                if hasattr(tunnel, 'kill'):
                    tunnel.kill()
            except Exception, e:
                logging.debug("WARNING: Failed to kill tunnel: %s" % e)

            del self.tunnels[key]

    def __del__(self):
        """Close/kill all ssh tunnels"""
        for key in self.tunnels:
            hostname, daemon = key.split('-', 1)
            self.kill_tunnel(hostname, daemon)


class Ssh(object):
    """A ssh tunnel over which to remotely run commands in a non-blocking way.

    Open a persistent ssh tunnel against a remote host, execute commands on the
    remote shell and read their answers. Alternatively, send commands to a
    remote daemon listening to the remote stdin, and read its answers on
    stdout/stderr.

    USAGE:

    from ssh import Ssh, SshException

    # USAGE 1: Talking with a remote shell:
    ssh = Ssh('foo')

    out = ssh.run(['do_this'])      # Execute 'do_this', get stdout in 'out'
    if ssh.retval != '0':           # Check the return value of 'do_this'
        ...

    print ssh.stdout                # Print all that was captured on stdout+stderr

    try:                 # Catch exceptions
        ssh.run(['sudo', 'rndc', 'reload'])
    except SshException, e:
        ...

    # USAGE 2: Talking with a remote daemon over stdin/stdout:

    # Open a ssh persistent ssh tunnel to foo, run the command
    # 'daemon --batch', and send queries to it via stdin. Assume we got a
    # complete reply when the captured output from stdout matches 'match'.  The
    # match regexp must match a complete response block. You will probably want
    # to use (?m) for multiline matching.
    ssh = Ssh('foo', daemon=['daemon', '--batch'], match=r'(OK|ERR)\s(.+[\\]\n)*([^\n\\]+)(?m)$')

    EXCEPTIONS:

    run() will raise a SshException in a number of cases, in particular when:
    - getting a host key verification failed (SshException)
    - cannot resolve hostname (SshException)
    - timed-out while connecting to hostname (SshException)
    - timed-out while waiting for a reply to a command (SshCmdTimeoutException)
    - the remote command failed (SshCmdFailedException) (not available in daemon mode)

    DEBUG:

    If logging.getEffectiveLevel() < 5 (ie if running with -vvvvv), the ssh tunnel
    will be run in verbose mode, so as to see what ssh does.
    """

    # Sleep for this time between each polling of ssh's stdout/stderr
    _sleep_delay = 0.2

    # A singleton pool of ssh tunnels
    _pool = SshTunnelPool()

    @staticmethod
    def _check_is_list_of_strings(obj, msg):
        """Check that a something is a list of strings"""
        if type(obj) is not ListType:
            raise TypeError(msg)
        for str in obj:
            if not isinstance(str, StringType):
                raise TypeError(msg)

    def __init__(self, hostname, daemon=None, match=None, ssh_options=None):
        """Constructor.
        hostname: dns name of the host to connect to.
        daemon: (optional) an array of commands to run in the remote shell to start a terminal daemon.
        match: (optional) a regexp that matches when a daemon's reply is complete.
        ssh_args: (optional) extra arguments to pass to the ssh tunnel (ex: -A)
        """

        if not hostname:
            raise TypeError("Undefined hostname in Ssh creator")
        if (match and not daemon) or (daemon and not match):
            raise TypeError("Either both daemon and match must be defined or none")
        if daemon:
            Ssh._check_is_list_of_strings(daemon, "Ssh expects daemon to be a list of command strings in Popen fashion")
        if match and not isinstance(match, StringType):
            raise TypeError("Ssh expects match to be a regexp")
        if ssh_options:
            Ssh._check_is_list_of_strings(ssh_options, "Ssh ssh_args to be a list of ssh string options")

        self.hostname = hostname
        self.daemon = daemon
        self.match = match
        self.retval = None
        self.stdout = None
        self.stderr = None
        self.ssh_options = ssh_options

    def _noblock_read_line(self, fh):
        """Read data from fh until blocks or gets a new line"""
        line = ''
        while (select.select([fh], [], [], 0)[0] != []):
            c = fh.read(1)
            if c == '':
                break
            elif c == '\r':
                continue
            elif c == '\n':
                break
            line += c
        return line

    def _communicate(self, string, match, timeout=None):
        """Send a string over the ssh tunnel and wait for answer"""

        # Open ssh tunnel to hypervisor
        tunnel = self._pool.get_tunnel(self.hostname, self.daemon, self.ssh_options)

        # What if the tunnel died?
        if tunnel.poll():
            logging.info('Ssh tunnel against %s died. Respawning it.' % self.hostname)
            self._pool.kill_tunnel(self.hostname, self.daemon)
            tunnel = self._pool.get_tunnel(self.hostname, self.daemon, self.ssh_options)

        tunnel.stdin.write(string + '\n')

        # Read from tunnel's stdout without blocking until command ends or a timeout is reached
        time_start = time.time()
        finished = False

        while not finished and (not timeout or (time.time() - time_start < timeout)):

            # Read data from stdout and stderr and append it to self.(stdout|stderr).
            line_out = self._noblock_read_line(tunnel.stdout)
            line_err = self._noblock_read_line(tunnel.stderr)

            if line_out == '' and line_err == '':
                logging.debug("sleeping %ss" % self._sleep_delay)
                time.sleep(self._sleep_delay)
                continue
            else:
                if line_out != '':
                    logging.debug("ssh-tunnel: got on stdout [%s]" % line_out)
                    self.stdout += line_out + '\n'
                if line_err != '':
                    logging.debug("ssh-tunnel: got on stderr [%s]" % line_err)
                    self.stderr += line_err + '\n'

            # If we got RET=<exit code>, the remote command has completed
            m = re.search(match, self.stdout, re.MULTILINE)
            if m:
                self.retval = m.group(1)
                finished = True
                break

            # Catch some known ssh issues
            if re.search(r'Host key verification failed.', line_err):
                self._pool.kill_tunnel(self.hostname, self.daemon)
                raise SshException('Host key verification failed against %s (run \'spvirtenvctl probe\'!)' % self.hostname)

            if re.search(r'Could not resolve hostname .*: Name or service not known', line_err):
                self._pool.kill_tunnel(self.hostname, self.daemon)
                raise SshException('Unknown host %s' % self.hostname)

            if re.search(r'connect to host .*: Connection timed out', line_err):
                self._pool.kill_tunnel(self.hostname, self.daemon)
                raise SshException('Timed-out while connecting to %s' % self.hostname)

# Note: use the timeout option, instead of relying on pattern matching to catch a blocking sudo password prompt
#            if re.search(r'\[sudo\] password for .+:', line):
#                self._kill_tunnel()
#                raise SshException('Sudo password required by %s' % self.hostname)

        if timeout and (time.time() - time_start >= timeout):
            logging.debug("Timeout while waiting max %s secs for reply to [%s]" % (timeout, string))
            self._pool.kill_tunnel(self.hostname, self.daemon)
            raise SshCmdTimeoutException('Timed-out while waiting for reply for command [%s]' % string)

    def run(self, cmd, timeout=None):
        """Run a command over ssh. Optionally timeout after a delay"""

        # Check argument types
        Ssh._check_is_list_of_strings(cmd, "Ssh.run(cmd) expects cmd to be a list of command strings in Popen fashion (cmd=%s)" % cmd)
        if timeout and not isinstance(timeout, IntType):
            raise TypeError("Ssh.run(cmd, timeout) expects timeout to be an integer giving a number of seconds")

        self.retval = None
        self.stdout = ''
        self.stderr = ''

        if self.daemon:
            # We are not running a remote shell but a daemon implementing some
            # form of RPC protocol. The other end is done when we find 'match'
            # in its output.
            str = ' '.join(cmd)

            logging.debug("---- sending: [%s]" % str)
            self._communicate(str, self.match, timeout)
            logging.debug("---- matched: [%s]" % self.retval)

        else:
            # We are executing directly in the remote bash.  Let's capture the
            # remote command's exit code.
            str = ' '.join(cmd) + ' && echo "RET="$? || echo "RET="$?'

            logging.debug("---- sending: [%s]" % str)
            self._communicate(str, r"(RET=\d+)\n", timeout)
            logging.debug("---- matched: [%s]" % self.retval)

            if self.retval is None:
                raise SshException('Failed to parse return value from output of [%s]' % str)

            _, self.retval = self.retval.split('=', 1)

            if self.retval != '0':
                logging.error("ERROR while running [%s] over ssh on %s.\nStdout:[%s]\nStderr:[%s]" % (str, self.hostname, self.stdout, self.stderr))
                raise SshCmdFailedException('An error occured while executing [%s] on [%s]. Return value was %s' % (str, self.hostname, self.retval))

        return self.stdout

    def kill(self):
        """Close the underlying ssh tunnel"""
        self._pool.kill_tunnel(self.hostname, self.daemon)
