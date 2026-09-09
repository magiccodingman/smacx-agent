# Production catalog JSON failure, turn 2

Incident gap-9b407d26d9e545d0a3e1ec9ff9d0debe stopped the Peacekeepers production query. The native reply failed JSON parsing at column 230/231. Both population fields streamed int8_t as a character. The bridge adapter returned a string error; choice enumeration assumed a mapping and concealed the original error with AttributeError.

Both native population fields now explicitly convert to int. Choice enumeration preserves string and structured errors, including semantic recovery guidance for invalid bases, without retrying transport failures or executing an action. Engine compatibility was reviewed and registered: only serialization changed, with no gameplay rule change.

Acceptance:
- choice_transport_error_test.py: 16 cases across production, base management, citizens and research; transport exceptions, string errors, structured errors and invalid bases; no mutation or blind retry.
- semantic_choice_binding_test.py: semantic/private binding and stale scope guards pass.
- production_context_delivery_test.py and production_queue_context_test.py pass. Missing native facts remain absent; the existing selection/completion explanation remains present.
- native_production_switch_live_test.py: isolated running native game, both population fields are integers; repeated catalogs preserve current state; guarded hurry adds 8 minerals for 19 energy, switch to Colony Pod produces the predicted 8-to-0 mineral change. This verifies catalog parsing, native execution and effect, not completed production or sovereign play after recovery.

The original campaign's turn-2 checkpoint subsequently restored both managed seats into the updated worker and was promoted from `save_verified` to `restore_tested`. Native restoration therefore succeeded. Sovereign restart then failed because the saved doctrine predated the reviewed engine fingerprint, and the portal incorrectly completed maintenance before attempting that restart. Its generic start-failure containment created an operator-pause incident and made the successfully restored campaign appear stuck.

The recovery confirmation now explicitly approves doctrine recompilation against the current reviewed contracts. That approval is durable in the maintenance payload and is forwarded to every recovered agent. Both capability-gap and operator-pause incidents use their correct recovery endpoint. Maintenance remains at step 4 while sovereigns restart and completes only when every managed seat has a running harness run. Portal restarts preserve this phase instead of replaying native restoration. The operator-pause dialog offers the same verified recovery action, so this state has a UI exit.

Portal validation: 88/88 tests pass, including a recovered-agent launch assertion that `recompile_doctrine=true` reaches the control service. Live recovery of the preserved campaign remains the final operational check after deployment.

The first live validation also exposed a coordinator race: a recovered peer can briefly report the native menu while the multiplayer session reconnects. The ordinary lost-session monitor started a second automatic restore over the still-active paired recovery. Both the observation branch and the automatic-recovery entry point now suppress that action while a capability recovery is queued or running. The original verified checkpoint remained retained through this failed overlap.
