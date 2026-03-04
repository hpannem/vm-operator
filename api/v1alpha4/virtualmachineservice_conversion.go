// © Broadcom. All Rights Reserved.
// The term "Broadcom" refers to Broadcom Inc. and/or its subsidiaries.
// SPDX-License-Identifier: Apache-2.0

package v1alpha4

import (
	apiconversion "k8s.io/apimachinery/pkg/conversion"
	ctrlconversion "sigs.k8s.io/controller-runtime/pkg/conversion"

	"github.com/vmware-tanzu/vm-operator/api/utilconversion"
	vmopv1 "github.com/vmware-tanzu/vm-operator/api/v1alpha5"
)

// ConvertTo converts this VirtualMachineService to the Hub version.
func (src *VirtualMachineService) ConvertTo(dstRaw ctrlconversion.Hub) error {
	dst := dstRaw.(*vmopv1.VirtualMachineService)
	if err := Convert_v1alpha4_VirtualMachineService_To_v1alpha5_VirtualMachineService(src, dst, nil); err != nil {
		return err
	}

	restored := &vmopv1.VirtualMachineService{}
	if ok, err := utilconversion.UnmarshalData(src, restored); err != nil || !ok {
		return err
	}

	dst.Spec.IPFamilies = restored.Spec.IPFamilies
	dst.Spec.IPFamilyPolicy = restored.Spec.IPFamilyPolicy

	return nil
}

// ConvertFrom converts the hub version to this VirtualMachineService.
func (dst *VirtualMachineService) ConvertFrom(srcRaw ctrlconversion.Hub) error {
	src := srcRaw.(*vmopv1.VirtualMachineService)
	if err := Convert_v1alpha5_VirtualMachineService_To_v1alpha4_VirtualMachineService(src, dst, nil); err != nil {
		return err
	}

	// Preserve Hub data on down-conversion except for metadata
	return utilconversion.MarshalData(src, dst)
}

func Convert_v1alpha5_VirtualMachineNetworkInterfaceSpec_To_v1alpha4_VirtualMachineNetworkInterfaceSpec(
	in *vmopv1.VirtualMachineNetworkInterfaceSpec, out *VirtualMachineNetworkInterfaceSpec, s apiconversion.Scope) error {

	// IPFamilyPolicy does not exist in v1alpha4 and is preserved via MarshalData.
	return autoConvert_v1alpha5_VirtualMachineNetworkInterfaceSpec_To_v1alpha4_VirtualMachineNetworkInterfaceSpec(in, out, s)
}

func Convert_v1alpha5_VirtualMachineServiceSpec_To_v1alpha4_VirtualMachineServiceSpec(
	in *vmopv1.VirtualMachineServiceSpec, out *VirtualMachineServiceSpec, s apiconversion.Scope) error {

	// IPFamilies and IPFamilyPolicy do not exist in v1alpha4 and are preserved via MarshalData.
	return autoConvert_v1alpha5_VirtualMachineServiceSpec_To_v1alpha4_VirtualMachineServiceSpec(in, out, s)
}
