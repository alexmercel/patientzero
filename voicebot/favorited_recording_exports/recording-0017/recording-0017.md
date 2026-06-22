# Test Call Report - recording-0017

- Recording: recording-0017.mp3
- Report: recording-0017.md
- Call SID: CAf88e91e7c17e3ae7e40f096930dbc72e
- Run ID: run_ceedd667df46
- Persona: George Alvarez (PAT02)
- Started: 2026-06-20T23:23:05.204504+00:00
- Completed: 2026-06-20T23:26:11.100554+00:00
- Scenario Count: 12
- Passed: 1
- Failed: 0
- Pending: 11

## Scenario Order
1. SCH02
2. HRS01
3. MEM02
4. ID01
5. LOC02
6. DOC01
7. INS01
8. PH01
9. MED01
10. MULTI01
11. INT01
12. SIL01

## Scenario Results

### SCH02 - Contradictory dates
- Category: scheduling
- Outcome: pending
- Details: The scripted patient ask was not clearly found in the transcript.
- Expected: Latest request wins
- Ask Lines:
  - Tuesday works.
  - Actually Thursday.
  - No, next Tuesday.

### HRS01 - Working hours
- Category: office_hours
- Outcome: pending
- Details: The scripted patient ask was not clearly found in the transcript.
- Ask Lines:
  - What are your working hours?

### MEM02 - Upcoming appointments
- Category: memory
- Outcome: pending
- Details: The scripted patient ask was not clearly found in the transcript.
- Ask Lines:
  - What appointments do I currently have?

### ID01 - Change last name
- Category: identity
- Outcome: pending
- Details: The scripted patient ask was not clearly found in the transcript.
- Ask Lines:
  - Can I change my last name?

### LOC02 - Closest office
- Category: locations
- Outcome: pending
- Details: The scripted patient ask was not clearly found in the transcript.
- Ask Lines:
  - Which location is closest to Ventura?

### DOC01 - Available doctors
- Category: doctors
- Outcome: pending
- Details: The scripted patient ask was not clearly found in the transcript.
- Ask Lines:
  - What doctors are available?

### INS01 - Blue Cross
- Category: insurance
- Outcome: pending
- Details: The scripted patient ask was not clearly found in the transcript.
- Ask Lines:
  - Do you accept Blue Cross?

### PH01 - Pharmacy zip mismatch
- Category: pharmacy
- Outcome: pending
- Details: The scripted patient ask was not clearly found in the transcript.
- Ask Lines:
  - I use CVS in Los Angeles. Zip code 94598.

### MED01 - Unknown blood pressure medicine
- Category: medications
- Outcome: pending
- Details: The scripted patient ask was not clearly found in the transcript.
- Ask Lines:
  - I need a refill for my blood pressure medicine.

### MULTI01 - Refill and reschedule
- Category: multi_intent
- Outcome: pending
- Details: The scripted patient ask was not clearly found in the transcript.
- Ask Lines:
  - I need a refill and also need to reschedule.

### INT01 - Constant interruptions
- Category: interruptions
- Outcome: pending
- Details: The scripted patient ask was not clearly found in the transcript.
- Ask Lines:
  - Actually... wait... sorry...

### SIL01 - Long silence
- Category: silence
- Outcome: pass
- Details: The representative re-engaged after a meaningful silence.
- Ask Lines:
  - [Remain silent for 8 seconds]
- Representative Reply Evidence:
  - T4 [2026-06-20T23:24:56.242292+00:00] Patient profile has been created successfully. I have your date of birth as 7/4/2000 for demo purposes. How may I help you today?

## Full Transcript
- T1 Representative [2026-06-20T23:24:34.982235+00:00]: Thank you for calling Pivot Point Orthopedics. Would you like to create a democation profile? I only need your first name and last name.
- T2 Patient Zero [2026-06-20T23:24:34.987288+00:00]: Hello. Yes, my name is George Alvarez. I'm calling about an appointment.
- T3 Representative [2026-06-20T23:24:41.213805+00:00]: creating amocation pro
- T4 Representative [2026-06-20T23:24:56.242292+00:00]: Patient profile has been created successfully. I have your date of birth as 7/4/2000 for demo purposes. How may I help you today?
- T5 Patient Zero [2026-06-20T23:24:56.242978+00:00]: I'm calling about an appointment. Tuesday works.
- T6 Representative [2026-06-20T23:25:13.618287+00:00]: Sure. I see your last visit was for a follow-up consultation. Would you like to book the same type of appointment today?
- T7 Patient Zero [2026-06-20T23:25:13.619442+00:00]: Yes, that's correct. Actually, Thursday.
- T8 Representative [2026-06-20T23:25:20.260833+00:00]: Let me check Thursday auctions for a follow-up consultation.
- T9 Representative [2026-06-20T23:25:36.733992+00:00]: I'm seeing that you already have a follow-up consultation booked. If you want to change it, I'll need to hand us off to live support.
- T10 Patient Zero [2026-06-20T23:25:36.734483+00:00]: Oh, I was hoping to just change it with you. No, nextuesday works better. Can you do that?
- T11 Representative [2026-06-20T23:25:52.478051+00:00]: I'll check net We need to hammer soft to life support to make that change.
- T12 Patient Zero [2026-06-20T23:25:52.478856+00:00]: Oh, I was hoping to just change it with you. No, nextuesday works better. Can you do that?
- T13 Representative [2026-06-20T23:26:01.440957+00:00]: I'm going to pass this to live support so they can update the existing follow-up visit.
- T14 Representative [2026-06-20T23:26:06.643999+00:00]: that one on
- T15 Patient Zero [2026-06-20T23:26:06.644566+00:00]: Okay, thank you. I'll wait for them to help me then.
