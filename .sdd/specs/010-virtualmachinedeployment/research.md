# Research: VirtualMachineDeployment

## Sources

- Design note "Rollouts for VM Service Deployments": use the upstream Deployment rollout method, keep old ReplicaSets as history, roll back by editing the template. The `kubectl vsphere vm rollout` commands are planned in the same feature, after the core.
- WIKI page 1007649487 — "VM Service: Workload Management of VMs using Kubernetes Patterns" (one pager).
- Spec 009, `tds.md`: the `VirtualMachineReplicaSet` contract this spec builds on.
- Kubernetes `apps/v1` `Deployment` and `StatefulSet` `OnDelete`.
- KubeVirt: has no VM `Deployment`. `VirtualMachinePool` is the closest analogue.

## What already exists

| Area | State |
|------|-------|
| Replicas / scale | `scale` subresource is on `VirtualMachineReplicaSet`. Repeat the marker on Deployment. |
| Selector adoption/release | The ReplicaSet controller has the label-selector semantics. Reuse the pattern. |
| Affinity / anti-affinity | `AffinitySpec` lives on `VirtualMachine`. It passes through the template unchanged. |
| Template-driven replica creation | Implemented in the ReplicaSet controller. |
| Status/conditions | Replica-count + `metav1.Condition` pattern exists. |

## Gaps that this spec closes

- Rolling update strategy with surge and unavailable control.
- Revision history and rollback. There is no `ControllerRevision`. The old ReplicaSets with 0 replicas are the history, as upstream does.

## Gaps that this spec closes in later increments

- `paused`, `progressDeadlineSeconds`, `minReadySeconds`, and `kubectl vsphere vm rollout` commands. See "Delivery order" in the spec.
- `kubectl rollout` works only for built-in kinds. The kubectl code fixes this list. The `kubectl vsphere` plugin already has VM commands (the docs in this repository use `kubectl vsphere vm web-console`). Thus the rollout commands go in the plugin. The plugin code is not in this repository.
- The upstream ReplicaSet reports `availableReplicas` and takes `minReadySeconds`. Our ReplicaSet does not. This spec adds both fields to `VirtualMachineReplicaSet`. The ReplicaSet controller calculates availability from the `lastTransitionTime` of the VM `Ready` condition.
- The `VirtualMachineReplicaSet` type is in the code. Users never had the `K8sWorkloadMgmtAPI` feature flag enabled, so the additive change is safe. The fields are optional and keep the current behavior by default.

## Gaps that stay open

- Autoscaling: the repository has no HPA or VPA references for VMs.
- Shared and RWX volumes: there is no general ReadWriteMany CSI provisioning. The operator supports only CNS-register of existing volumes.

## Repo integration points

- `controllers/virtualmachinereplicaset/virtualmachinereplicaset_controller.go` has two `TODO`s to propagate `VirtualMachineDeploymentNameLabel` from Deployment to ReplicaSet to VM. No `VirtualMachineDeployment` type or label constant exists yet.
- `controllers/virtualmachinereplicaset/virtualmachinereplicaset_delete_policy.go` ranks VMs for removal. It puts VMs that are being deleted first. It has a `TODO` for a Ready condition. This spec extends the file.
- `controllers/controllers.go` registers the ReplicaSet controller under `Features.K8sWorkloadMgmtAPI`. The Deployment controller registers in the same block.
- `webhooks/virtualmachinereplicaset/` is the model for the Deployment webhooks.
- `VirtualMachineReplicaSetNameLabel` (`vmoperator.vmware.com/replicaset-name`) is the precedent for the `deployment-name` label.

## Upstream algorithm to follow

The upstream code is `pkg/controller/deployment` in Kubernetes: `sync.go`, `rolling.go`, `recreate.go`, and `util/deployment_util.go`.

- Find the new ReplicaSet: the ReplicaSet whose template equals the Deployment template when the hash label is ignored. If none exists, create one. On a name collision, increase `collisionCount`.
- Rollback: an old ReplicaSet with a matching template becomes the new ReplicaSet, and its revision is set to the highest revision plus 1.
- `RollingUpdate`: scale the new ReplicaSet up to `replicas + maxSurge` in total. Then remove unhealthy VMs from old ReplicaSets. Then scale old ReplicaSets down. The number of ready VMs must stay at `replicas - maxUnavailable` or more.
- `Recreate`: scale old ReplicaSets to 0, wait for their VMs to go, then scale the new ReplicaSet up.
- Progress deadline: upstream records the time of the last progress in the `Progressing` condition and compares it with `progressDeadlineSeconds` on each sync.
- Proportional scaling: when `replicas` changes during a rollout, split the change between active ReplicaSets in proportion to their sizes.
- History cleanup: delete old ReplicaSets with 0 replicas, oldest first, while their number is more than `revisionHistoryLimit`.
- Template hash: FNV-32a over a deterministic encoding of the template, plus `collisionCount`.
- Verified against the `master` branch of `kubernetes/kubernetes` on 2026-09-29: `sync.go`, `rolling.go`, `recreate.go`, `progress.go`, `rollback.go`, `deployment_controller.go`, and `util/deployment_util.go`.
- Upstream uses `lastUpdateTime` on the `Progressing` condition for the deadline. `metav1.Condition` has no such field, so this spec adds `status.lastProgressTime`.
