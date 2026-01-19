// © Broadcom. All Rights Reserved.
// The term "Broadcom" refers to Broadcom Inc. and/or its subsidiaries.
// SPDX-License-Identifier: Apache-2.0

package v1alpha1

import (
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// NetworkInterfaceType is the type of network interface.
type NetworkInterfaceType string

const (
	// NetworkInterfaceTypeVMXNet3 is the VMXNet3 network interface type.
	NetworkInterfaceTypeVMXNet3 NetworkInterfaceType = "vmxnet3"
)

// NetworkInterfaceIPAssignmentMode is the IP assignment mode for a network interface.
type NetworkInterfaceIPAssignmentMode string

const (
	// NetworkInterfaceIPAssignmentModeDHCP indicates DHCP is used for IP assignment.
	NetworkInterfaceIPAssignmentModeDHCP NetworkInterfaceIPAssignmentMode = "DHCP"
	// NetworkInterfaceIPAssignmentModeStaticPool indicates static IP pool is used for IP assignment.
	NetworkInterfaceIPAssignmentModeStaticPool NetworkInterfaceIPAssignmentMode = "STATICPOOL"
	// NetworkInterfaceIPAssignmentModeNone indicates no IP assignment.
	NetworkInterfaceIPAssignmentModeNone NetworkInterfaceIPAssignmentMode = "NONE"
)

// NetworkInterfaceIPFamilyPolicy defines the IP family policy for a network interface.
type NetworkInterfaceIPFamilyPolicy string

const (
	// NetworkInterfaceIPFamilyPolicyIPv4Only indicates only IPv4 addresses will be allocated.
	NetworkInterfaceIPFamilyPolicyIPv4Only NetworkInterfaceIPFamilyPolicy = "IPv4Only"
	// NetworkInterfaceIPFamilyPolicyIPv6Only indicates only IPv6 addresses will be allocated.
	NetworkInterfaceIPFamilyPolicyIPv6Only NetworkInterfaceIPFamilyPolicy = "IPv6Only"
	// NetworkInterfaceIPFamilyPolicyDualStack indicates both IPv4 and IPv6 addresses will be allocated.
	NetworkInterfaceIPFamilyPolicyDualStack NetworkInterfaceIPFamilyPolicy = "DualStack"
)

// NetworkInterfaceConditionType is the type of condition for a network interface.
type NetworkInterfaceConditionType string

const (
	// NetworkInterfaceReady indicates the network interface is ready.
	NetworkInterfaceReady NetworkInterfaceConditionType = "Ready"
	// NetworkInterfaceFailure indicates the network interface has failed.
	NetworkInterfaceFailure NetworkInterfaceConditionType = "Failure"
)

// NetworkInterfaceCondition defines the condition of a network interface.
type NetworkInterfaceCondition struct {
	// Type is the type of the condition.
	Type NetworkInterfaceConditionType `json:"type"`
	// Status is the status of the condition.
	Status corev1.ConditionStatus `json:"status"`
	// Reason is the reason for the condition's last transition.
	// +optional
	Reason string `json:"reason,omitempty"`
	// Message is a human-readable message indicating details about the transition.
	// +optional
	Message string `json:"message,omitempty"`
}

// IPConfig defines the IP configuration for a network interface.
type IPConfig struct {
	// IP is the IP address.
	IP string `json:"ip,omitempty"`
	// SubnetMask is the subnet mask.
	SubnetMask string `json:"subnetMask,omitempty"`
	// Gateway is the gateway address.
	Gateway string `json:"gateway,omitempty"`
	// IPFamily is the IP family (IPv4 or IPv6).
	IPFamily corev1.IPFamily `json:"ipFamily,omitempty"`
}

// NetworkInterfaceSpec defines the desired state of a NetworkInterface.
type NetworkInterfaceSpec struct {
	// NetworkName is the name of the network to attach to.
	// +optional
	NetworkName string `json:"networkName,omitempty"`
	// Type is the type of network interface.
	// +optional
	Type NetworkInterfaceType `json:"type,omitempty"`
	// MacAddress is the MAC address of the network interface.
	// +optional
	MacAddress string `json:"macAddress,omitempty"`
	// IPFamilyPolicy specifies the IP family policy for this network interface.
	// +optional
	IPFamilyPolicy NetworkInterfaceIPFamilyPolicy `json:"ipFamilyPolicy,omitempty"`
}

// NetworkInterfaceStatus defines the observed state of a NetworkInterface.
type NetworkInterfaceStatus struct {
	// Conditions is a list of conditions for the network interface.
	// +optional
	Conditions []NetworkInterfaceCondition `json:"conditions,omitempty"`
	// NetworkID is the ID of the network.
	// +optional
	NetworkID string `json:"networkID,omitempty"`
	// MacAddress is the MAC address of the network interface.
	// +optional
	MacAddress string `json:"macAddress,omitempty"`
	// ExternalID is the external ID of the network interface.
	// +optional
	ExternalID string `json:"externalID,omitempty"`
	// IPAssignmentMode is the IP assignment mode.
	// +optional
	IPAssignmentMode NetworkInterfaceIPAssignmentMode `json:"ipAssignmentMode,omitempty"`
	// IPConfigs is a list of IP configurations.
	// +optional
	IPConfigs []IPConfig `json:"ipConfigs,omitempty"`
}

// +genclient
// +k8s:deepcopy-gen:interfaces=k8s.io/apimachinery/pkg/runtime.Object

// NetworkInterface is the Schema for the networkinterfaces API.
// +k8s:openapi-gen=true
type NetworkInterface struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   NetworkInterfaceSpec   `json:"spec,omitempty"`
	Status NetworkInterfaceStatus `json:"status,omitempty"`
}

// +k8s:deepcopy-gen:interfaces=k8s.io/apimachinery/pkg/runtime.Object

// NetworkInterfaceList contains a list of NetworkInterface.
type NetworkInterfaceList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []NetworkInterface `json:"items"`
}
