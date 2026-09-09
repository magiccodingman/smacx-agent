# Production catalog JSON failure, turn 2

Incident gap-9b407d26d9e545d0a3e1ec9ff9d0debe stopped the Peacekeepers production query. The native reply failed JSON parsing at column 230/231. Both population fields streamed int8_t as a character. The bridge adapter returned a string error; choice enumeration assumed a mapping and concealed the original error with AttributeError.

Both native population fields now explicitly convert to int. Choice enumeration preserves string and structured errors, including semantic recovery guidance for invalid bases, without retrying transport failures or executing an action. Engine compatibility was reviewed and registered: only serialization changed, with no gameplay rule change.

Acceptance:
- choice_transport_error_test.py: 16 cases across production, base management, citizens and research; transport exceptions, string errors, structured errors and invalid bases; no mutation or blind retry.
- semantic_choice_binding_test.py: semantic/private binding and stale scope guards pass.
- production_context_delivery_test.py and production_queue_context_test.py pass. Missing native facts remain absent; the existing selection/completion explanation remains present.
- native_production_switch_live_test.py: isolated running native game, both population fields are integers; repeated catalogs preserve current state; guarded hurry adds 8 minerals for 19 energy, switch to Colony Pod produces the predicted 8-to-0 mineral change. This verifies catalog parsing, native execution and effect, not completed production or sovereign play after recovery.

The original campaign remains frozen. Its saved paired checkpoints have not been restored by this validation. Full campaign recovery and subsequent autonomous play are separate acceptance steps.
