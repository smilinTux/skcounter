# Fleet rollout

The canonical release, canary install, rollback, and exposure procedures are in [SOP.md section 5](../SOP.md). This document governs the later staged activation of central reporting and fleet timers.

## Gates

Fleet-wide activation proceeds through separate reviewed work for the central collector and the eligible-node timer rollout. Version 0.2.0 supplies the collector, CapAuth report capability, normalized snapshot generator, node outbox, user timers, retention, rollback, and monitoring surfaces. Activation still requires the matching SKCapstone card and per-node evidence.

## Recommended sequence

1. Complete the `chiap08` local command canary with outbound submission blocked.
2. Implement and test snapshot normalization with synthetic session fixtures.
3. Qualify the append-only collector on `chiap04` with synthetic reports only.
4. Qualify CapAuth allow, deny, expiry, revocation, replay, malformed payload, oversize payload, collector outage, clock-skew, retention, monitoring, and rollback behavior.
5. Activate one `chiap08` user timer and observe local scan duration, CPU, disk, outbox, and exact central totals.
6. Add one `chiap04` operator user to prove a second node and principal.
7. Add one WSL workstation to prove offline queueing and resume behavior.
8. Install the passive package across remaining in-scope nodes.
9. Activate per-user timers only where harness discovery or an explicit eligibility declaration exists.
10. Add the SKGateway adapter as a separate measurement lane.
11. Compare harness and gateway lanes without combining them, then define any correlation projection separately.

## Expected freshness

An enabled timer starts after five minutes and then runs every 15 minutes with up to two minutes of randomized delay. Under normal collector and network health, the dashboard should receive the first acknowledgement within seven minutes of activation and later data within 17 minutes of the preceding run. Collection duration adds to those bounds. An on-demand `systemctl --user start skcounter-edge.service` bypasses the timer wait.

## Rollback

Roll back an active wave by disabling only the SKCounter timer for the affected principal. Preserve unacknowledged outbox records for investigation unless the retention owner approves deletion. The local harness and SKGateway continue operating because neither depends on SKCounter.

The package can remain installed while collection is disabled. Remove it with `scripts/uninstall-user.sh` only after confirming no timer points to the command. Uninstallation preserves local configuration and outbox state by default.

## Stop conditions

Stop the active wave on any raw transcript egress, credential output, cross-user read, unsigned report acceptance, duplicate inflation, unexpected Tokscale social request, material harness slowdown, unbounded outbox growth, schema ambiguity, or failure to distinguish measurement lanes.

## Acceptance evidence per node

- Node and principal identity.
- Exact SKCounter and backend versions.
- Package and lockfile hashes.
- Detected source types without source paths.
- Timer definition and next run.
- Local policy-denial tests.
- Synthetic report hash and collector receipt.
- Retry and duplicate receipt behavior.
- CPU, memory, scan duration, and outbox size.
- Disable and uninstall commands.
- Linked SKCapstone card and CMDB update.
