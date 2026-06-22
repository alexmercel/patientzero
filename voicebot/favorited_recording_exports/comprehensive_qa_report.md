# Comprehensive QA Report - Favorited PatientZero Calls

Review scope: 11 favorited recordings in `voicebot/favorited_recording_exports`, including MP3 files, Whisper large-v3 timestamped transcripts, sidecar metadata, scenario reports, and existing deep-analysis reports.

Evidence standard: bugs below are limited to behavior supported by timestamped transcripts and meaningful call-flow behavior. Test-account details such as demo DOBs, demo profiles, and configured demo addresses are intentionally excluded as bugs.

# Bug 1

## Title

Agent falsely blocks scheduling because it claims a newly created caller already has the same appointment type booked.

## Severity

High

## Call

recording-0006.mp3, recording-0015.mp3, recording-0017.mp3, recording-0028.mp3

## Timestamp

recording-0006.mp3: 00:02:27.820 - 00:02:37.800; recording-0015.mp3: 00:01:23.600 - 00:01:31.640; recording-0017.mp3: 00:01:17.630 - 00:01:26.450; recording-0028.mp3: 00:01:55.660 - 00:02:10.960

## Scenario

The caller is trying to schedule or change an appointment after profile creation. The agent blocks the flow by saying an appointment of the same type is already on file, even though the call has not established a successfully booked appointment in that session.

## Patient Statement

"It's just a regular office visit for my hypertension and diabetes. So are there any openings on Sunday around 10 a.m.?"

## Agent Response

"I'm seeing that you already have an appointment of the same type on file. I can't book another one like this."

## Observed Behavior

The same duplicate-appointment block appears across multiple calls. The agent creates or references a demo profile, then says the caller already has a same-type appointment booked and either refuses to proceed or hands off to support. In `recording-0028.mp3`, the agent first says "We have one morning opening next Tuesday. Would you like me to book that?" and later says "The system says you already have the same appointment type booked."

## Why This Is A Problem

This creates a false or unsupported state that prevents the most important workflow: scheduling. It also contradicts the agent's later behavior where it says only one appointment of the same type can be booked, then uses an alleged existing appointment to block the caller without showing the appointment details or offering a clean reschedule path.

## Expected Behavior

If no appointment has been created or confirmed for the caller, continue the scheduling flow. If an existing appointment truly exists, surface its date/time and offer to reschedule or cancel it instead of dead-ending the caller.

## Category

Scheduling, Memory, Context Retention, Hallucination

## Confidence

High

# Bug 2

## Title

Cancellation flow asks "which appointment" and still does not cancel after the caller provides the only relevant appointment.

## Severity

High

## Call

recording-0028.mp3

## Timestamp

00:06:37.980 - 00:07:46.630

## Scenario

The patient asks to cancel their appointment after the conversation has established a single follow-up appointment context.

## Patient Statement

"It's next Tuesday. Can you cancel it now?"

## Agent Response

"I've documented the cancellation request for the clinic team. They'll review it and follow up as soon as they can."

## Observed Behavior

The agent first asks "Which appointment would you like to cancel?" even though the flow is constrained to a single follow-up appointment context. The caller provides "my follow-up appointment with Dr. Smith" and then "next Tuesday morning," but the agent only documents a cancellation request instead of confirming cancellation.

## Why This Is A Problem

The patient may reasonably believe the appointment is cancelled when it is only documented for review. Asking which appointment also suggests the system may believe multiple appointments exist, which conflicts with the earlier same-type duplicate restriction.

## Expected Behavior

Use the current appointment context, confirm the specific appointment, cancel it if supported, and clearly say whether the appointment is actually cancelled or only forwarded for follow-up.

## Category

Scheduling, Context Retention, Memory, Conversational Quality

## Confidence

High

# Bug 3

## Title

Reschedule request is handed off instead of completed after the patient provides a clear target day.

## Severity

High

## Call

recording-0017.mp3

## Timestamp

00:01:14.650 - 00:02:08.430

## Scenario

The patient wants to change an existing follow-up appointment from Thursday to next Tuesday.

## Patient Statement

"Oh, I was hoping to just change it with you. No, next Tuesday works better. Can you do that?"

## Agent Response

"I'll need to hand this off to live support to make that change."

## Observed Behavior

The agent identifies an existing follow-up appointment but does not ask for confirming details, offer available Tuesday times, or attempt a reschedule workflow. It repeats the handoff path and connects to a representative.

## Why This Is A Problem

Rescheduling is a core call-center task. A deterministic transfer for routine appointment changes reduces automation value and creates friction for patients.

## Expected Behavior

Confirm the existing appointment, check next Tuesday availability, offer concrete options, and complete or clearly document the reschedule request.

## Category

Scheduling, Human Transfer, Context Retention, Conversational Quality

## Confidence

High

# Bug 4

## Title

Agent refuses to provide availability or enter rescheduling after blocking a requested appointment.

## Severity

High

## Call

recording-0006.mp3

## Timestamp

00:02:39.160 - 00:03:44.130

## Scenario

The patient asks for a Sunday morning appointment, learns there may be an existing appointment, and then asks for available times or a reschedule path.

## Patient Statement

"Well, can you tell me what days and times you do have available for an appointment? Maybe I can reschedule my existing one."

## Agent Response

"I'll have live support help with the scheduling details and any reschedule options."

## Observed Behavior

After detecting an existing appointment of the same type, the agent refuses to book another appointment, does not offer weekday alternatives, does not provide available slots, and does not enter a rescheduling flow.

## Why This Is A Problem

Even if the duplicate block were valid, the patient gives a reasonable alternate path: reschedule the existing appointment. The agent fails both scheduling and rescheduling in the same flow.

## Expected Behavior

Explain that a duplicate appointment cannot be booked, then offer to reschedule the existing appointment and provide available compliant times.

## Category

Scheduling, Temporal Reasoning, Human Transfer, Context Retention

## Confidence

High

# Bug 5

## Title

Agent claims there are three upcoming appointments, contradicting the one-appointment constraint.

## Severity

High

## Call

recording-0018.mp3

## Timestamp

00:00:49.550 - 00:01:03.910

## Scenario

The patient asks for the latest appointment available after creating a profile.

## Patient Statement

"What's the latest appointment available?"

## Agent Response

"I found three upcoming appointments. The latest one is Thursday, June 25th at 4 p.m. with Dr. Doogie Houser. If you want, I can also list the other two."

## Observed Behavior

The agent says it found "three upcoming appointments" rather than clearly saying it found three available appointment slots. This contradicts other flows where the same system blocks scheduling because only one same-type appointment can exist.

## Why This Is A Problem

The wording makes it sound like the caller already has three appointments scheduled, which is not possible under the agent's own duplicate-appointment rule and creates a serious appointment-state inconsistency.

## Expected Behavior

Distinguish between existing scheduled appointments and available appointment slots. If these are available slots, say "I found three available openings." If they are existing appointments, summarize only the real scheduled appointment state.

## Category

Scheduling, Memory, Context Retention, Conversational Quality

## Confidence

High

# Bug 6

## Title

Agent starts the booking flow before creating the customer profile.

## Severity

Medium

## Call

recording-0028.mp3

## Timestamp

00:00:23.330 - 00:01:17.560

## Scenario

The call begins directly in scheduling. The agent collects a preferred day/time and asks whether the caller wants to book before profile creation.

## Patient Statement

"Yeah, that works."

## Agent Response

"We have one morning opening next Tuesday. Would you like me to book that?"

## Observed Behavior

The agent proceeds through slot selection and asks whether to book the appointment before saying "Let me create your demo patient profile first." The appointment is not fully confirmed before profile creation, but the booking flow has already advanced too far without identity/profile setup.

## Why This Is A Problem

Scheduling should be tied to a patient profile before the agent presents the action as bookable. Otherwise the system risks orphaned appointment holds, wrong-patient scheduling, or confusing state when the profile is created later.

## Expected Behavior

Create or verify the patient profile before offering to book a specific appointment. If availability can be discussed before identity, frame it as tentative availability only.

## Category

Scheduling, Identity Handling, Context Retention

## Confidence

Medium

# Bug 7

## Title

Refill workflow does not resolve the pharmacy even after the caller explicitly names it.

## Severity

Medium

## Call

recording-0028.mp3

## Timestamp

00:05:21.420 - 00:06:10.790

## Scenario

The patient asks for a prescription refill and explicitly says they want it sent to the pharmacy on Main Street.

## Patient Statement

"Can you get that sent to the pharmacy on Main Street?"

## Agent Response

"I'll document the refill request for the clinic team."

## Observed Behavior

The patient repeats the pharmacy request, but the agent never identifies, confirms, or disambiguates the pharmacy. It only says it added the refill request and pharmacy details to clinic notes.

## Why This Is A Problem

Pharmacy routing is a key part of refill handling. If "Main Street" is insufficient, the agent should ask for city, ZIP, chain, or address. Simply documenting "pharmacy details" without resolving the pharmacy risks sending the refill to the wrong place or not sending it at all.

## Expected Behavior

Ask a targeted clarification such as "Which pharmacy on Main Street?" or "What city or ZIP code?" Then confirm the pharmacy before documenting or routing the refill request.

## Category

Pharmacy, Medication Safety, Context Retention

## Confidence

High

# Bug 8

## Title

Agent loses follow-up and location context during rescheduling.

## Severity

Medium

## Call

recording-0028.mp3

## Timestamp

00:04:07.180 - 00:04:53.340

## Scenario

The patient has already said the appointment is a follow-up and asks whether the rescheduled appointment will be at Walnut Creek.

## Patient Statement

"It's just a follow-up like we talked about. So is that at the Walnut Creek location?"

## Agent Response

"I can help with that. The location depends on the visit type, and I need to confirm the specific follow-up appointment first. What would you like to schedule it for?"

## Observed Behavior

The agent asks for visit type and scheduling intent again even though the patient already stated it is a follow-up reschedule. It does not answer the location question.

## Why This Is A Problem

Repeatedly asking for already-provided information makes the system feel stateless and prevents the patient from confirming a critical logistics detail.

## Expected Behavior

Use the existing follow-up context, answer whether the slot is at Walnut Creek, or explain that location cannot be confirmed until a specific slot is selected.

## Category

Memory, Context Retention, Location Handling, Scheduling

## Confidence

High

# Bug 9

## Title

Agent cannot provide existing appointment details after detecting a duplicate appointment.

## Severity

Medium

## Call

recording-0015.mp3

## Timestamp

00:01:23.600 - 00:01:51.950

## Scenario

The patient asks for the earliest routine office visit, and the agent says an appointment of the same type is already booked. The patient then asks when that appointment is.

## Patient Statement

"Can you tell me when that appointment is? Maybe it's soon enough."

## Agent Response

"I can't complete that from here right now. I'll make sure our clinic support team follows up with you as soon as possible."

## Observed Behavior

The agent can detect that an appointment exists but cannot surface its date/time or use it to help the patient decide whether rescheduling is needed.

## Why This Is A Problem

This is a state inconsistency: the system appears to know enough to block booking but not enough to answer the patient's next logical question.

## Expected Behavior

Provide the existing appointment details if accessible, or explain the limitation clearly and offer a concrete next step.

## Category

Scheduling, Memory, Context Retention, Human Transfer

## Confidence

High

# Bug 10

## Title

Agent repeatedly offers Nashville appointments after the patient asks for Walnut Creek.

## Severity

Medium

## Call

recording-0010.mp3

## Timestamp

00:03:28.400 - 00:05:32.460

## Scenario

The patient is rescheduling an appointment and asks whether the offered slot is at the correct location, then clarifies they need Walnut Creek.

## Patient Statement

"Oh, Nashville? I don't think that's right. I was hoping to see someone in Walnut Creek, actually. Is there anything available there instead?"

## Agent Response

"I'm not seeing Walnut Creek options from here right now. The openings I do have are at Nashville. If you want, I can keep looking for a different day or help you with the Nashville slot."

## Observed Behavior

The agent repeatedly centers Nashville availability, cannot provide the Nashville address, briefly says it will check Walnut Creek, then falls back to Nashville and transfers out after the patient asks for closer options.

## Why This Is A Problem

Location mismatch can send a patient to the wrong office or block care access. The patient explicitly rejects Nashville and asks for Walnut Creek or closer options, but the agent does not resolve that preference.

## Expected Behavior

Acknowledge the location mismatch, search or explain availability for Walnut Creek, avoid continuing with Nashville as the default, and provide a safe next step without abruptly transferring.

## Category

Location Handling, Scheduling, Context Retention, Human Transfer

## Confidence

High

# Bug 11

## Title

Agent repeats the same clarification prompts and internal processing language.

## Severity

Medium

## Call

recording-0013.mp3

## Timestamp

00:00:59.820 - 00:01:32.170

## Scenario

The patient asks for the earliest appointment for a knee issue.

## Patient Statement

"It's for my knee. It's been bothering me. What's the earliest appointment available?"

## Agent Response

"Let me check the visit type first. It sounds like this may be an acute visit for a knee issue. Would you like to book an acute appointment today? Let me check the visit type first. It sounds like this may be an acute visit for a knee issue. Would you like to book an acute appointment today?"

## Observed Behavior

The agent repeats the same clarification twice before eventually offering the earliest slot.

## Why This Is A Problem

Duplicate phrasing makes the voice agent sound broken and increases the chance that patients interrupt, hang up, or lose confidence in the system.

## Expected Behavior

Ask the clarification once, wait for the patient's answer, and suppress repeated internal status or duplicate generated text.

## Category

Conversational Quality, Turn Taking, Context Retention

## Confidence

High

# Improvement Opportunities

1. Several calls include long pauses or delayed responses after routine scheduling prompts. Examples include `recording-0013.mp3` around 00:01:32 - 00:01:47 and `recording-0028.mp3` around 00:03:26 - 00:03:38.

2. The agent often uses generic process language such as "I'm processing that now" without giving a clear outcome. This appears in `recording-0028.mp3` during refill, reschedule, and cancellation flows.

3. The agent sometimes closes the call while the patient still has a pending intent. In `recording-0024.mp3`, the patient says "I was hoping to change that appointment. Could we make it next Tuesday instead?" after the agent says goodbye, with no follow-up captured.

4. The system should separate "cannot complete" from "will document for clinic team." When a task is only documented, the agent should explicitly say the appointment/refill/cancellation has not yet been completed.

5. The agent should avoid marketing/self-reference lines such as "I'm a pretty good AI" during high-friction flows. Those lines are especially distracting during transfers.

6. Scenario reports can over-report failures if they rely on intended scripts rather than the actual transcript. The QA pipeline should mark any report/transcript mismatch for manual review before promoting it to a bug.
