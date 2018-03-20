#!/usr/bin/env python

"""Device Automated Qualification testing framework"""

from __future__ import print_function

import logging
import os
import time

from host import DockerHost, DAQHost

from mininet import log as minilog
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.cli import CLI


logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


def controllerIp(switch):
    container = os.environ['DAQ_CONTAINER']
    if not container:
        return '127.0.0.1'
    defaultRoute = switch.cmd("ip route | fgrep default | awk '{ print $3 }'").strip()
    return defaultRoute


def addHost(net, switch, name, cls=DAQHost):
    host = net.addHost(name, cls)
    link = net.addLink(switch, host)
    if net.built:
        host.configDefault()
        switch.attach(link.intf2.name)
    return host


def daqCmd(switch, cmd):
    return switch.cmd(cmd)


def createNetwork():

    logging.debug("Creating miniet...")
    net = Mininet()

    logging.debug("Adding switch...")
    switch = net.addSwitch('s1', cls=OVSSwitch)

    logging.debug("Starting faucet controller...")
    daqCmd(switch, 'cmd/faucet')

    targetIp = controllerIp(switch)
    logging.debug("Adding controller at %s" % targetIp)
    controller = net.addController( 'c1', controller=RemoteController, ip=targetIp, port=6633 )

    logging.debug("Adding hosts...")
    h1 = addHost(net, switch, 'h1')
    h2 = addHost(net, switch, 'h2')
    h3 = addHost(net, switch, 'h3', cls=DockerHost)

    logging.debug("Starting mininet...")
    net.start()

    logging.debug("Waiting for system to settle...")
    time.sleep(2)

    logging.debug("Ping test h1->h2")
    print(h1.cmd('ping -c1', h2.IP(), '> /dev/null || echo ping FAILED'), end='')
    logging.debug("Ping test h2->h1")
    print(h2.cmd('ping -c1', h1.IP(), '> /dev/null || echo ping FAILED'), end='')
    logging.debug("Ping test h1->h3")
    print(h1.cmd('ping -c1', h3.IP(), '> /dev/null || echo ping FAILED'), end='')
    logging.debug("Ping test h3->h1")
    print(h3.cmd('ping -c1', h1.IP(), '> /dev/null || echo ping FAILED'), end='')

    logging.debug("Stopping host h2")
    h2.terminate()
    time.sleep(1)

    '''logging.debug("Ping test h1->h2 (should fail)")
    print(h1.cmd('ping -c1', h2.IP(), '> /dev/null || echo ping FAILED'), end='')'''

    CLI(net)

    logging.debug("Stopping mininet...")
    net.stop()



if __name__ == '__main__':
    minilog.setLogLevel('info')
    if os.getuid() != 0:
        logger.debug("You are NOT root")
    elif os.getuid() == 0:
        createNetwork()
