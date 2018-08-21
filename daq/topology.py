"""Faucet-specific topology module"""

import logging
import os
import yaml

LOGGER = logging.getLogger('topology')

class FaucetTopology(object):
    """Topology manager specific to FAUCET configs"""

    DEFAULT_NETWORK_FILE = "misc/faucet.yaml"
    OUTPUT_NETWORK_FILE = "inst/faucet.yaml"
    MAC_PLACEHOLDER = "@src_mac:"
    DNS_PLACEHOLDER = "@dns:"
    PORT_ACL_NAME_FORMAT = "dp_%s_port_%d_acl"
    INST_FILE_PREFIX = "inst/"
    DP_ACL_FILE_FORMAT = "dp_%s_port_acls.yaml"
    PORT_ACL_FILE_FORMAT = "port_acls/dp_%s_port_%d_acl.yaml"
    TEMPLATE_FILE_FORMAT = "inst/acl_templates/template_%s_acl.yaml"
    RULES_KEY_FORMAT = "@acl:template_%s_acl"
    DEVICE_SPECS_FILE = "inst/device_specs.json"
    INCOMING_ACL_FORMAT = "dp_%s_incoming_acl"
    PORTSET_ACL_FORMAT = "dp_%s_portset_acl"

    def __init__(self, config, pri):
        self._mac_map = {}
        self.config = config
        self.pri = pri
        self.sec_port = 7
        self.sec_name = 'sec'
        self._device_specs = self._load_file(self.DEVICE_SPECS_FILE)
        self.topology = self._load_base_network_topology()
        assert self.topology, 'Could not find network config file'

    def initialize(self):
        """Initialize this topology"""
        LOGGER.debug("Converting existing network topology...")
        self._convert_network_config()
        self.generate_acls()

    def start(self):
        """Start this instance"""
        LOGGER.info("Starting faucet...")
        output = self.pri.cmd('cmd/faucet && echo SUCCESS')
        if not output.strip().endswith('SUCCESS'):
            LOGGER.info('Faucet output: %s', output)
            assert False, 'Faucet startup failed'

    def stop(self):
        """Stop this instance"""
        LOGGER.debug("Stopping faucet...")
        self.pri.cmd('docker kill daq-faucet')

    def _load_file(self, filename):
        if not os.path.isfile(filename):
            LOGGER.debug("File %s does not exist, skipping.", filename)
            return None
        LOGGER.debug("Loading file %s", filename)
        with open(filename) as stream:
            return yaml.safe_load(stream)

    def get_sec_intf(self):
        """Return the external interface for seconday"""
        return self.topology['dps']['pri']['interfaces'][1].get('name')

    def get_sec_dpid(self):
        """Return the secondary dpid"""
        return self.topology['dps']['sec']['dp_id']

    def get_sec_port(self):
        """Return the stacking network port on the secondary"""
        interfaces = self.topology['dps']['sec']['interfaces']
        for interface in interfaces:
            if 'stack' in interfaces[interface]:
                stack = interfaces[interface]['stack']
                assert stack['dp'] == 'pri', 'stack interface should be pri'
                return int(interface)
        return None

    def get_device_intfs(self):
        """Return list of secondary device interfaces"""
        device_intfs = []
        interfaces = self.topology['dps']['sec']['interfaces']
        for interface in interfaces:
            intf = interfaces[interface]
            if 'stack' not in intf:
                port = int(interface)
                name = intf['name'] if 'name' in intf else 'sec-%d' % port
                device_intfs.append({
                    'port': port,
                    'name': name
                })
        return device_intfs

    def _convert_network_config(self):
        self._add_acl_includes()
        self._write_network_topology()

    def direct_port_traffic(self, target_mac, target):
        """Direct traffic from a given mac to specified port set"""
        port_no = target['port'] if target else None
        if target is None and target_mac in self._mac_map:
            del self._mac_map[target_mac]
        elif target is not None and target_mac not in self._mac_map:
            self._mac_map[target_mac] = target
        else:
            LOGGER.debug('Ignoring no-change in port status for %s on %d', target_mac, port_no)
            return
        self.generate_acls(port=port_no)

    def _ensure_entry(self, root, key, value):
        if key not in root:
            root[key] = value
        return root[key]

    def _add_acl_includes(self):
        self._add_pri_includes()
        for range_port in range(1, self.sec_port):
            self._add_port_include(range_port)

    def _add_pri_includes(self):
        switch_name = self.pri.name
        include = self._ensure_entry(self.topology, 'include', [])
        pri_filename = self.DP_ACL_FILE_FORMAT % switch_name
        include.append(pri_filename)
        pri_interface = self.topology['dps']['pri']['interfaces'][1]
        assert 'acl_in' not in pri_interface, 'acl_in already defined on pri interface'
        pri_interface['acl_in'] = self.INCOMING_ACL_FORMAT % switch_name
        interface_ranges = self.topology['dps']['pri']['interface_ranges']
        for interface_range in interface_ranges:
            port_interface = interface_ranges[interface_range]
            assert 'acl_in' not in port_interface, 'acl_in already defined on %s' % interface_range
            port_interface['acl_in'] = self.PORTSET_ACL_FORMAT % switch_name

    def _add_port_include(self, range_port):
        rules = []
        if not self._append_acl_template(rules, 'raw'):
            return
        sec_filename = self.PORT_ACL_FILE_FORMAT % (self.sec_name, range_port)
        include_optional = self._ensure_entry(self.topology, 'include-optional', [])
        include_optional.append(sec_filename)
        interface = self.topology['dps'][self.sec_name]['interfaces'][range_port]
        assert 'acl_in' not in interface, 'acl_in already defined for %s' % sec_filename
        acl_name = self.PORT_ACL_NAME_FORMAT % (self.sec_name, range_port)
        interface['acl_in'] = acl_name
        self._ensure_entry(self.topology, 'acls', {})
        acls = self.topology['acls']
        assert acl_name not in acls, 'acl %s already defined in faucet.yaml' % acl_name
        acls[acl_name] = rules

    def _load_base_network_topology(self):
        config_file = self.config.get('network_config')
        if config_file:
            LOGGER.info('Loading local network config from %s', config_file)
            return self._load_file(config_file)
        LOGGER.info('Loading default network config from %s', self.DEFAULT_NETWORK_FILE)
        return self._load_file(self.DEFAULT_NETWORK_FILE)

    def _write_network_topology(self):
        LOGGER.info('Writing network config to %s', self.OUTPUT_NETWORK_FILE)
        with open(self.OUTPUT_NETWORK_FILE, "w") as output_stream:
            yaml.safe_dump(self.topology, stream=output_stream)

    def generate_acls(self, port=None):
        """Generate all ACLs required for dynamic system operation"""
        self._generate_pri_acls()
        self._generate_port_acls(port=port)

    def _generate_pri_acls(self):
        switch_name = self.pri.name

        incoming_acl = []
        portset_acl = []

        for target_mac in self._mac_map:
            target = self._mac_map[target_mac]
            ports = range(target['range'][0], target['range'][1])
            self._add_acl_pri_rule(incoming_acl, dl_src=target_mac, in_vlan=10, ports=ports)
            self._add_acl_pri_rule(portset_acl, dl_dst=target_mac, out_vlan=10, port=1)

        self._add_acl_pri_rule(incoming_acl, allow=0)
        self._add_acl_pri_rule(portset_acl, allow=1)

        acls = {}
        acls[self.INCOMING_ACL_FORMAT % switch_name] = incoming_acl
        acls[self.PORTSET_ACL_FORMAT % switch_name] = portset_acl

        pri_acls = {}
        pri_acls["acls"] = acls

        filename = self.INST_FILE_PREFIX + self.DP_ACL_FILE_FORMAT % switch_name
        LOGGER.debug("Writing updated pri acls to %s", filename)
        with open(filename, "w") as output_stream:
            yaml.safe_dump(pri_acls, stream=output_stream)

    def _maybe_apply(self, target, keyword, origin, source=None):
        source_keyword = source if source else keyword
        if source_keyword in origin:
            target[keyword] = origin[source_keyword]

    def _add_acl_pri_rule(self, acl, **kwargs):
        in_vlan = {}
        if 'in_vlan' in kwargs:
            in_vlan['pop_vlans'] = True
            in_vlan['vlan_vid'] = kwargs['in_vlan']

        output = {}
        self._maybe_apply(output, 'port', kwargs)
        self._maybe_apply(output, 'ports', kwargs)
        self._maybe_apply(output, 'vlan_vid', kwargs, 'out_vlan')
        self._maybe_apply(output, 'pop_vlans', in_vlan)

        actions = {}
        if output:
            actions['output'] = output
        self._maybe_apply(actions, 'allow', kwargs)

        subrule = {}
        subrule["actions"] = actions
        self._maybe_apply(subrule, "dl_src", kwargs)
        self._maybe_apply(subrule, "dl_dst", kwargs)
        self._maybe_apply(subrule, "vlan_vid", in_vlan)

        rule = {}
        rule["rule"] = subrule

        acl.append(rule)

    def _generate_port_acls(self, port=None):
        if port:
            self._generate_port_acl(port=port)
        else:
            for range_port in range(1, self.sec_port):
                self._generate_port_acl(port=range_port)

    def _generate_port_acl(self, port=None):
        has_mapping = False
        rules = []
        if self._device_specs:
            for target_mac in self._mac_map:
                if self._mac_map[target_mac]['port'] == port:
                    self._add_acl_port_rules(rules, target_mac=target_mac)
                    has_mapping = True

        filename = self.INST_FILE_PREFIX + self.PORT_ACL_FILE_FORMAT % (self.sec_name, port)
        if has_mapping:
            assert self._append_acl_template(rules, 'baseline'), 'Missing ACL template baseline'
            LOGGER.debug("Writing port acl file %s", filename)
            self._write_port_acl(port, rules, filename)
        elif os.path.isfile(filename):
            LOGGER.debug("Removing unused port acl file %s", filename)
            os.remove(filename)

    def _write_port_acl(self, port, rules, filename):
        acl_name = self.PORT_ACL_NAME_FORMAT % (self.sec_name, port)
        acls = {}
        acls[acl_name] = rules
        port_acl = {}
        port_acl['acls'] = acls
        with open(filename, "w") as output_stream:
            yaml.safe_dump(port_acl, stream=output_stream)

    def _add_acl_port_rules(self, rules, target_mac):
        mac_map = self._device_specs['macAddrs']
        if target_mac not in mac_map:
            LOGGER.info("No device spec found for %s", target_mac)
            device_type = 'default'
        else:
            device_info = mac_map[target_mac]
            device_type = device_info['type'] if 'type' in device_info else 'default'
        LOGGER.info("Processing acl template for %s/%s", target_mac, device_type)
        self._append_acl_template(rules, device_type, target_mac)

    def _sanitize_mac(self, mac_addr):
        return mac_addr.replace(':', '')

    def device_group_for(self, target_mac):
        """Find the target device group for the given address"""
        if not self._device_specs:
            return self._sanitize_mac(target_mac)
        mac_map = self._device_specs['macAddrs']
        if target_mac in mac_map and 'group' in mac_map[target_mac]:
            return mac_map[target_mac]['group']
        return self._sanitize_mac(target_mac)

    def _append_acl_template(self, rules, template, target_mac=None):
        filename = self.TEMPLATE_FILE_FORMAT % template
        template_acl = self._load_file(filename)
        if not template_acl:
            return False
        template_key = self.RULES_KEY_FORMAT % template
        for acl in template_acl['acls'][template_key]:
            new_rule = acl['rule']
            self._resolve_template_field(new_rule, 'dl_src', target_mac=target_mac)
            self._resolve_template_field(new_rule, 'nw_dst')
            rules.append(acl)
        return True

    def _resolve_template_field(self, rule, field, target_mac=None):
        if field not in rule:
            return
        placeholder = rule[field]
        if placeholder.startswith(self.MAC_PLACEHOLDER):
            rule[field] = target_mac
        elif placeholder.startswith(self.DNS_PLACEHOLDER):
            del rule[field]