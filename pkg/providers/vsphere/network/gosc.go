// © Broadcom. All Rights Reserved.
// The term “Broadcom” refers to Broadcom Inc. and/or its subsidiaries.
// SPDX-License-Identifier: Apache-2.0

package network

import (
	"fmt"
	"net"

	"github.com/go-logr/logr"
	vimtypes "github.com/vmware/govmomi/vim25/types"
)

func GuestOSCustomization(results NetworkInterfaceResults) ([]vimtypes.CustomizationAdapterMapping, error) {
	var logger logr.Logger
	return GuestOSCustomizationWithLogger(results, logger)
}

func GuestOSCustomizationWithLogger(results NetworkInterfaceResults, logger logr.Logger) ([]vimtypes.CustomizationAdapterMapping, error) {
	mappings := make([]vimtypes.CustomizationAdapterMapping, 0, len(results.Results))

	for i, r := range results.Results {
		if logger.GetSink() != nil {
			logger.V(4).Info("Processing adapter for GOSC customization",
				"adapterIndex", i,
				"macAddress", r.MacAddress,
				"dhcp4", r.DHCP4,
				"dhcp6", r.DHCP6,
				"noIPAM", r.NoIPAM,
				"ipConfigsCount", len(r.IPConfigs))
			for j, ipConfig := range r.IPConfigs {
				logger.V(4).Info("IPConfig details",
					"adapterIndex", i,
					"ipConfigIndex", j,
					"ipCIDR", ipConfig.IPCIDR,
					"isIPv4", ipConfig.IsIPv4,
					"gateway", ipConfig.Gateway)
			}
		}

		adapter := vimtypes.CustomizationIPSettings{
			// Per-adapter is only supported on Windows. Linux only supports the global and ignores this field.
			DnsServerList: r.Nameservers,
		}

		if logger.GetSink() != nil {
			logger.V(5).Info("Before IPv4 switch", "adapterIndex", i, "adapterIp", adapter.Ip)
		}

		switch {
		case r.DHCP4:
			adapter.Ip = &vimtypes.CustomizationDhcpIpGenerator{}
			if logger.GetSink() != nil {
				logger.V(4).Info("Set adapter.Ip to DHCP4 generator", "adapterIndex", i)
			}
		case r.NoIPAM: //nolint:revive
			// adapter.Ip = &vimtypes.CustomizationDisableIpV4{} // TODO
			if logger.GetSink() != nil {
				logger.V(4).Info("NoIPAM=true, adapter.Ip remains nil", "adapterIndex", i)
			}
		default:
			// GOSC doesn't support multiple IPv4 address per interface so use the first one.
			// Old code only ever set one gateway so do the same here too.
			foundIPv4 := false
			for _, ipConfig := range r.IPConfigs {
				if !ipConfig.IsIPv4 {
					continue
				}

				ip, ipNet, err := net.ParseCIDR(ipConfig.IPCIDR)
				if err != nil {
					return nil, err
				}
				subnetMask := net.CIDRMask(ipNet.Mask.Size())

				adapter.Ip = &vimtypes.CustomizationFixedIp{IpAddress: ip.String()}
				adapter.SubnetMask = net.IP(subnetMask).String()
				if ipConfig.Gateway != "" {
					adapter.Gateway = []string{ipConfig.Gateway}
				}
				foundIPv4 = true
				if logger.GetSink() != nil {
					logger.V(4).Info("Set adapter.Ip to fixed IPv4",
						"adapterIndex", i,
						"ipAddress", ip.String(),
						"subnetMask", adapter.SubnetMask,
						"gateway", ipConfig.Gateway)
				}
				break
			}
			if !foundIPv4 && logger.GetSink() != nil {
				logger.V(4).Info("No IPv4 config found, adapter.Ip remains nil", "adapterIndex", i)
			}

		}

		if logger.GetSink() != nil {
			logger.V(5).Info("After IPv4 switch", "adapterIndex", i, "adapterIp", adapter.Ip)
		}

		switch {
		case r.DHCP6:
			adapter.IpV6Spec = &vimtypes.CustomizationIPSettingsIpV6AddressSpec{
				Ip: []vimtypes.BaseCustomizationIpV6Generator{
					&vimtypes.CustomizationDhcpIpV6Generator{},
				},
			}
			if logger.GetSink() != nil {
				logger.V(4).Info("Set adapter.IpV6Spec to DHCP6 generator", "adapterIndex", i)
			}
		default:
			for _, ipConfig := range r.IPConfigs {
				if ipConfig.IsIPv4 {
					continue
				}

				ip, ipNet, err := net.ParseCIDR(ipConfig.IPCIDR)
				if err != nil {
					return nil, err
				}
				ones, _ := ipNet.Mask.Size()

				if adapter.IpV6Spec == nil {
					adapter.IpV6Spec = &vimtypes.CustomizationIPSettingsIpV6AddressSpec{}
				}
				adapter.IpV6Spec.Ip = append(adapter.IpV6Spec.Ip, &vimtypes.CustomizationFixedIpV6{
					IpAddress:  ip.String(),
					SubnetMask: int32(ones), //nolint:gosec // disable G115
				})
				if ipConfig.Gateway != "" {
					adapter.IpV6Spec.Gateway = append(adapter.IpV6Spec.Gateway, ipConfig.Gateway)
				}
				if logger.GetSink() != nil {
					logger.V(4).Info("Added IPv6 to adapter.IpV6Spec",
						"adapterIndex", i,
						"ipAddress", ip.String(),
						"subnetMask", ones,
						"gateway", ipConfig.Gateway)
				}
			}
		}

		if logger.GetSink() != nil {
			logger.V(5).Info("After IPv6 switch",
				"adapterIndex", i,
				"adapterIp", adapter.Ip,
				"hasIpV6Spec", adapter.IpV6Spec != nil)
		}

		// When only IPv6 is configured (no IPv4 addresses, no DHCP4), the vSphere API
		// requires adapter.Ip to be set. Set it to DHCP as a fallback.
		conditionCheck := adapter.Ip == nil && !r.NoIPAM && (adapter.IpV6Spec != nil || r.DHCP6)
		if logger.GetSink() != nil {
			logger.V(4).Info("IPv6-only fix condition check",
				"adapterIndex", i,
				"adapterIpIsNil", adapter.Ip == nil,
				"noIPAM", r.NoIPAM,
				"hasIpV6Spec", adapter.IpV6Spec != nil,
				"dhcp6", r.DHCP6,
				"conditionMet", conditionCheck)
		}

		if conditionCheck {
			adapter.Ip = &vimtypes.CustomizationDhcpIpGenerator{}
			if logger.GetSink() != nil {
				logger.Info("Applied IPv6-only fix: set adapter.Ip to DHCP generator",
					"adapterIndex", i,
					"macAddress", r.MacAddress)
			}
		}

		if logger.GetSink() != nil {
			logger.V(4).Info("Final adapter state",
				"adapterIndex", i,
				"macAddress", r.MacAddress,
				"adapterIp", adapter.Ip,
				"hasIpV6Spec", adapter.IpV6Spec != nil)
			if adapter.Ip != nil {
				switch v := adapter.Ip.(type) {
				case *vimtypes.CustomizationDhcpIpGenerator:
					logger.V(5).Info("Adapter IP type", "adapterIndex", i, "type", "CustomizationDhcpIpGenerator")
				case *vimtypes.CustomizationFixedIp:
					logger.V(5).Info("Adapter IP type", "adapterIndex", i, "type", "CustomizationFixedIp", "address", v.IpAddress)
				default:
					logger.V(5).Info("Adapter IP type", "adapterIndex", i, "type", fmt.Sprintf("%T", v))
				}
			}
		}

		mappings = append(mappings, vimtypes.CustomizationAdapterMapping{
			MacAddress: r.MacAddress,
			Adapter:    adapter,
		})
	}

	return mappings, nil
}
