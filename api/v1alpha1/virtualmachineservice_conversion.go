// © Broadcom. All Rights Reserved.
// The term "Broadcom" refers to Broadcom Inc. and/or its subsidiaries.
// SPDX-License-Identifier: Apache-2.0

package v1alpha1

import (
	apiconversion "k8s.io/apimachinery/pkg/conversion"
	ctrlconversion "sigs.k8s.io/controller-runtime/pkg/conversion"

	"github.com/vmware-tanzu/vm-operator/api/utilconversion"
	"github.com/vmware-tanzu/vm-operator/api/v1alpha5"
)

// ConvertTo converts this VirtualMachineService to the Hub version.
func (src *VirtualMachineService) ConvertTo(dstRaw ctrlconversion.Hub) error {
	dst := dstRaw.(*v1alpha5.VirtualMachineService)
	if err := Convert_v1alpha1_VirtualMachineService_To_v1alpha5_VirtualMachineService(src, dst, nil); err != nil {
		return err
	}

	restored := &v1alpha5.VirtualMachineService{}
	if ok, err := utilconversion.UnmarshalData(src, restored); err != nil || !ok {
		return err
	}

	dst.Spec.IPFamilies = restored.Spec.IPFamilies
	dst.Spec.IPFamilyPolicy = restored.Spec.IPFamilyPolicy

	return nil
}

// ConvertFrom converts the hub version to this VirtualMachineService.
func (dst *VirtualMachineService) ConvertFrom(srcRaw ctrlconversion.Hub) error {
	src := srcRaw.(*v1alpha5.VirtualMachineService)
	if err := Convert_v1alpha5_VirtualMachineService_To_v1alpha1_VirtualMachineService(src, dst, nil); err != nil {
		return err
	}

	// Preserve Hub data on down-conversion except for metadata
	return utilconversion.MarshalData(src, dst)
}

// ConvertTo converts this VirtualMachineServiceList to the Hub version.
func (src *VirtualMachineServiceList) ConvertTo(dstRaw ctrlconversion.Hub) error {
	dst := dstRaw.(*v1alpha5.VirtualMachineServiceList)
	return Convert_v1alpha1_VirtualMachineServiceList_To_v1alpha5_VirtualMachineServiceList(src, dst, nil)
}

// ConvertFrom converts the hub version to this VirtualMachineServiceList.
func (dst *VirtualMachineServiceList) ConvertFrom(srcRaw ctrlconversion.Hub) error {
	src := srcRaw.(*v1alpha5.VirtualMachineServiceList)
	return Convert_v1alpha5_VirtualMachineServiceList_To_v1alpha1_VirtualMachineServiceList(src, dst, nil)
}

func Convert_v1alpha5_VirtualMachineServiceSpec_To_v1alpha1_VirtualMachineServiceSpec(
	in *v1alpha5.VirtualMachineServiceSpec, out *VirtualMachineServiceSpec, s apiconversion.Scope) error {

	// IPFamilies and IPFamilyPolicy do not exist in v1alpha1 and are preserved via MarshalData.
	return autoConvert_v1alpha5_VirtualMachineServiceSpec_To_v1alpha1_VirtualMachineServiceSpec(in, out, s)
}
