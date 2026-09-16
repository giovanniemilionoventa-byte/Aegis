# Phase 18 live attack harnesses

These attack a **running** Aegis deployment with hand-built requests. The
browser is deliberately not involved: the frontend is not the security boundary,
so the only question worth asking is what the API does when the caller does not
cooperate.

They are not part of the pytest suite, because they need the Docker stack up and
they create real tenants and agents in it. Recorded results, with the deployment
they were run against, are in `docs/evidence/phase18_api_attacks.json`.

## Control plane

Run from the host, against the published control-plane port. It expects a
`uidrive/part1.json` next to it holding a second tenant's operator credentials
and agent token (`email`, `password`, `token`) — the walkthrough writes that
file; otherwise create a second organization and fill it in by hand.

```
python3 scripts/phase18/attack_control_plane.py
```

Exit status is non-zero if any expectation failed.

## Gateway

The enforcement gateway has no host port by design, so this runs from a
throwaway container on the agent network:

```
docker run --rm --network aegis_agent_net \
  -e VICTIM_TOKEN=... -e ATTACKER_TOKEN=... -e VICTIM_RUN=... -e OPERATOR_JWT=... \
  -v "$PWD/scripts/phase18/attack_gateway.py:/tmp/a.py:ro" \
  aegis-agent:latest python /tmp/a.py
```

`VICTIM_TOKEN` and `ATTACKER_TOKEN` must be agent credentials from **different**
tenants, and `VICTIM_RUN` a verification run queued for the victim's agent and
not yet claimed — stop the victim's agent process first, or it will claim its own
job before the attack runs.

## A warning about these harnesses

Three of the checks here were wrong when first written, and two of them *passed*
while being wrong. An expectation that is too narrow reports a correct refusal as
a hole; an expectation that is satisfied by an early error reports nothing at all
as a pass. If you add a case, make it fail first on purpose.
