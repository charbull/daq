"""Module for running docker-container tests"""

import datetime
import logging

from clib import docker_host

LOGGER = logging.getLogger('docker')

class DockerTest():
    """Class for running docker tests"""

    IMAGE_NAME_FORMAT = 'daq/test_%s'
    CONTAINER_PREFIX = 'daq'

    def __init__(self, runner, target_port, tmpdir, test_name):
        self.target_port = target_port
        self.tmpdir = tmpdir
        self.test_name = test_name
        self.runner = runner
        self.host_name = '%s%02d' % (test_name, self.target_port)
        self.docker_log = None
        self.docker_host = None
        self.callback = None
        self.start_time = None

    def start(self, port, params, callback):
        """Start the docker test"""
        LOGGER.debug('Target port %d starting docker test %s', self.target_port, self.test_name)

        self.start_time = datetime.datetime.now()
        self.callback = callback

        env_vars = ["TARGET_NAME=" + self.host_name,
                    "TARGET_IP=" + params['target_ip'],
                    "TARGET_MAC=" + params['target_mac'],
                    "GATEWAY_IP=" + params['gateway_ip'],
                    "GATEWAY_MAC=" + params['gateway_mac']]

        if 'local_ip' in params:
            env_vars += ["LOCAL_IP=" + params['local_ip'],
                         "SWITCH_PORT=" + params['switch_port'],
                         "SWITCH_IP=" + params['switch_ip']]

        vol_maps = [params['scan_base'] + ":/scans"]

        conf_base = params.get('conf_base')
        if conf_base:
            vol_maps += [conf_base + ":/config"]

        dev_base = params.get('dev_base')
        if dev_base:
            vol_maps += [dev_base + ":/device"]

        image = self.IMAGE_NAME_FORMAT % self.test_name
        LOGGER.debug("Target port %d running docker test %s", self.target_port, image)
        cls = docker_host.make_docker_host(image, prefix=self.CONTAINER_PREFIX)
        host = self.runner.add_host(self.host_name, port=port, cls=cls, env_vars=env_vars,
                                    vol_maps=vol_maps, tmpdir=self.tmpdir)
        self.docker_host = host
        try:
            LOGGER.debug("Target port %d activating docker test %s", self.target_port, image)
            host = self.docker_host
            pipe = host.activate(log_name=None)
            # Docker tests don't use DHCP, so manually set up DNS.
            host.cmd('echo nameserver $GATEWAY_IP > /etc/resolv.conf')
            self.docker_log = host.open_log()
            self.runner.monitor_stream(self.host_name, pipe.stdout, copy_to=self.docker_log,
                                       hangup=self._docker_complete,
                                       error=self._docker_error)
        except:
            host.terminate()
            self.runner.remove_host(host)
            raise
        LOGGER.info("Target port %d test %s running", self.target_port, self.test_name)

    def _docker_error(self, e):
        LOGGER.error('Target port %d docker error: %s', self.target_port, e)
        if self._docker_finalize() is None:
            LOGGER.warning('Target port %d docker already terminated.', self.target_port)
        else:
            self.callback(exception=e)

    def _docker_finalize(self):
        if self.docker_host:
            LOGGER.debug('Target port %d docker finalize', self.target_port)
            self.runner.remove_host(self.docker_host)
            return_code = self.docker_host.terminate()
            self.docker_host = None
            self.docker_log.close()
            self.docker_log = None
            return return_code
        return None

    def _docker_complete(self):
        try:
            return_code = self._docker_finalize()
            exception = None
        except Exception as e:
            return_code = -1
            exception = e
            LOGGER.exception(e)
        delay = (datetime.datetime.now() - self.start_time).total_seconds()
        LOGGER.debug("Target port %d docker complete, return=%d (%s)",
                     self.target_port, return_code, exception)
        if return_code:
            LOGGER.info("Target port %d test %s failed %ss: %s %s",
                        self.target_port, self.test_name, delay, return_code, exception)
        else:
            LOGGER.info("Target port %d test %s passed %ss",
                        self.target_port, self.test_name, delay)
        self.callback(return_code=return_code, exception=exception)
