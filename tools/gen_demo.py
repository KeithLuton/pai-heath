"""Generates src/data/tracker_demo.json — synthetic, deterministic (seed 55)."""
import json, random, datetime as dt, statistics as st
random.seed(55)
D=dt.date
def ds(d): return d.isoformat()
start=D(2026,8,25); lis=D(2026,9,8); today=D(2026,10,6)
bp=[]; d=start
while d<=today:
    for slot in ("AM","PM"):
        pre = d<lis
        sb = 148 if pre else (141 if d<lis+dt.timedelta(days=5) else 136)
        db = 92 if pre else (89 if d<lis+dt.timedelta(days=5) else 86)
        bp.append({"id":f"BP-{ds(d)}-{slot}","date":ds(d),"slot":slot,"systolic":round(random.gauss(sb,5)),"diastolic":round(random.gauss(db,3.5)),"device_class":"HOME_CUFF","standing":"FIRST_PERSON"})
    d+=dt.timedelta(days=1)
pre=[r for r in bp if lis-dt.timedelta(days=14)<=D.fromisoformat(r["date"])<lis]
post=[r for r in bp if lis<=D.fromisoformat(r["date"])<lis+dt.timedelta(days=14)]
recent=[r for r in bp if D.fromisoformat(r["date"])>today-dt.timedelta(days=14)]
def avg(rs,k): return round(sum(r[k] for r in rs)/len(rs),1)
def spread(rs):
    by={}
    for r in rs: by.setdefault(r["date"],[]).append(r["systolic"])
    return round(st.pstdev([sum(v)/len(v) for v in by.values()]),1)
daily=[]; d=start
while d<=today:
    daily.append({"date":ds(d),"resting_hr":round(random.gauss(68,2.2)),"sleep_hours":round(random.gauss(6.6,0.6),1),"steps":int(random.gauss(6200,1500)),"active_minutes":max(0,int(random.gauss(22,9))),"device_class":"WATCH_PPG","standing":"DEVICE"})
    d+=dt.timedelta(days=1)
glu=[]; d=today-dt.timedelta(days=13)
while d<=today:
    glu.append({"date":ds(d),"fasting_mg_dl":round(random.gauss(121,5)),"device_class":"HOME_METER","standing":"FIRST_PERSON"}); d+=dt.timedelta(days=1)
gh=sum(1 for g in glu if g["fasting_mg_dl"]>=126)
wk=sum(r["active_minutes"] for r in daily[-7:])
G="ACC/AHA high blood pressure guideline"
demo={
 "synthetic":True,
 "notice":"Synthetic demo data for UI development. Not a real person. Every value is precomputed; the UI computes nothing epistemic.",
 "as_of":ds(today),
 "user":{"display_name":"Demo user","age":55,"sex":"male"},
 "settings":{"data_path":"REVIEW","device_dose_confirmation":False,"ai_mode":"PAi","live_available":False,"local_model_installed":True},
 "lanes":[
  {"area":"Blood pressure","lane":1,"summary":f"Home average {avg(recent,'systolic'):.0f}/{avg(recent,'diastolic'):.0f} over the last 14 days.","history":"A check-in prompt showed on 2026-09-01 (14-day average above 140/90). Resolved: clinician visit 2026-09-08, lisinopril started.","escalates_if":"Average back at 140/90 or higher, or any reading at or above 180/120 with symptoms."},
  {"area":"Blood sugar","lane":1,"summary":f"Fasting meter readings averaged {avg(glu,'fasting_mg_dl'):.0f} mg/dL over 14 days; {gh} of 14 were 126 or higher.","history":"Watching. A check-in prompt shows at 5 of 14 days at 126 or higher.","escalates_if":"5 or more of 14 fasting readings at 126 or higher."},
  {"area":"Kidney function","lane":None,"summary":"This year's labs haven't been entered.","history":"Last metabolic panel entered 2025-08-14.","escalates_if":None}
 ],
 "prompts":[
  {"id":"PR-1","kind":"DOSE","text":"Your inhaler sensor recorded a puff at 7:42 this morning. Log it?","options":["Log it","That wasn't me"],"safety_line":None},
  {"id":"PR-2","kind":"DOSE","text":"No 8:00 dose of lisinopril has been logged yet. Did you take it?","options":["Yes","No","Not sure"],"safety_line":"If you're not sure whether you took it, check your medication's instructions or ask your pharmacist before taking another dose."},
  {"id":"PR-3","kind":"MEASURE","text":"Two evening blood pressure readings are due. Sit with your back supported, rest 5 minutes, arm at heart level.","options":["Log readings"],"safety_line":None}
 ],
 "notices":[{"id":"N-LABS","text":"This year's labs haven't been entered. Kidney function, cholesterol, thyroid and similar can't be monitored, and suggestions are based on your device and logged data only."}],
 "medications":[
  {"id":"MED-1","name":"Lisinopril","dose":"10 mg once daily","start":"2026-09-08","start_basis":"Logged by you; matches the clinic visit","adherence":"Logged on 13 of the last 14 days","sensor":"Pill cap: opened in the dose window on 12 of 14 days",
   "verification":{"state":"NOT_DETECTABLE","text":"Blood pressure is measured twice a day, which is too sparse to show a post-dose pattern. Reminders use your schedule only."},
   "confirmation":{"sensor_class":"CONTAINER_ACCESS","status_off":"EXPLAINED","status_on":"EXPLAINED","label":"Cap opened in the dose window. Opening is recorded, not swallowing."}},
  {"id":"MED-2","name":"Metformin","dose":"1000 mg twice daily","start":None,"start_basis":"Start date not found in your records or logs","adherence":"Logged 27 of 28 doses","sensor":None,
   "verification":{"state":"NOT_DETECTABLE","text":"No reliable pattern found for this medication in your data. Reminders use your schedule only."},"confirmation":None},
  {"id":"MED-3","name":"Albuterol inhaler","dose":"2 puffs as needed","start":"2025-03-02","start_basis":"Logged by you","adherence":"Used 5 times in the last 14 days","sensor":"Inhaler sensor: 5 of 5 uses recorded",
   "verification":{"state":"QUALIFIED","text":"After a logged dose, your heart rate usually rises about 9 bpm within 30 minutes. Learned from 21 logged doses; this separates clearly from times without a dose."},
   "confirmation":{"sensor_class":"DIRECT_DELIVERY","status_off":"EXPLAINED","status_on":"VALIDATED","label":"Puff recorded by the inhaler sensor in the dose window."}}
 ],
 "change_windows":[
  {"id":"CW-1","anchor":"Lisinopril start on 2026-09-08 (logged by you)","measure":"Home systolic blood pressure","target_set_by":"your clinician, visit of 2026-09-08",
   "pre":{"days":14,"readings":len(pre),"mean_sys":avg(pre,'systolic'),"mean_dia":avg(pre,'diastolic'),"daily_spread_sys":spread(pre)},
   "post":{"days":14,"readings":len(post),"mean_sys":avg(post,'systolic'),"mean_dia":avg(post,'diastolic')},
   "difference_vs_spread":"LARGER","adherence":"Doses logged on 13 of 14 days",
   "challenge":[
     {"item":"Other changes in the window","text":"Lower-sodium days began 2026-09-10. Their effect can't be separated from the medication's."},
     {"item":"Regression to the mean","text":"The start followed several readings above 150, so some drop would be expected anyway."},
     {"item":"Measurement change","text":"None found: same cuff, same times of day."},
     {"item":"Adherence","text":"From your logs: 13 of 14 days."}]}
 ],
 "suggestions":[
  {"id":"SG-1","area":"Blood pressure","title":"DASH-style eating","text":"Build meals around vegetables, fruit, whole grains and low-fat dairy.","state":"ACTIVE","effect":"Average systolic drop around 11 mmHg in people with high blood pressure.","source":G,"verified":False,"adopted":False},
  {"id":"SG-2","area":"Blood pressure","title":"Lower sodium","text":"Aim for under 2,300 mg of sodium a day.","state":"ACTIVE","effect":"Around 5–6 mmHg.","source":G,"verified":False,"adopted":True,"adopted_on":"2026-09-10","progress":"Logged sodium was under 2,300 mg on 9 of the last 14 days."},
  {"id":"SG-3","area":"Blood pressure","title":"Aerobic activity","text":"Work up to about 150 minutes a week. Brisk walking counts.","state":"ACTIVE","effect":"Around 5–8 mmHg.","source":G,"verified":False,"adopted":True,"adopted_on":"2026-09-15","progress":f"{wk} active minutes in the last 7 days, from your watch."},
  {"id":"SG-4","area":"Blood pressure","title":"Limit alcohol","text":"No more than 2 drinks a day; fewer is better.","state":"ACTIVE","effect":"Around 4 mmHg.","source":G,"verified":False,"adopted":False,"note":"Your medication list was checked: alcohol is also noted on the metformin label."},
  {"id":"SG-5","area":"Blood pressure","title":"More potassium from food","text":"Beans, potatoes, leafy greens and similar foods.","state":"HELD","held_reason":"You take lisinopril, and kidney function isn't known from this year's labs. Ask your clinician before increasing potassium.","source":G,"verified":False},
  {"id":"SG-6","area":"Blood pressure","title":"Salt substitute","text":"Potassium-based salt substitutes.","state":"HELD","held_reason":"Blocked while you take lisinopril, and held until kidney function is known.","source":G,"verified":False},
  {"id":"SG-7","area":"Nutrition","title":"Higher-protein plan","text":"Raise daily protein toward a higher target.","state":"HELD","held_reason":"Available once kidney function is known.","source":"Dietary Reference Intakes","verified":False}
 ],
 "raises_bp":[{"item":"Ibuprofen","logged":"3 times in the last 14 days","text":"Some of what you've logged can raise blood pressure. Ask your pharmacist about alternatives."}],
 "labs":{"annual_status":"OVERDUE","last_entered":"2025-08-14",
  "ask_to_include":[
    {"test":"Metabolic panel (kidney, liver, electrolytes)","why":"Everyone, yearly","source":"Annual baseline (§27)"},
    {"test":"A1c","why":"Diabetes is on your record","source":"ADA Standards of Care"},
    {"test":"Urine albumin-to-creatinine ratio","why":"Diabetes and high blood pressure","source":"ADA Standards of Care"},
    {"test":"Lipid panel","why":"Age and blood pressure history","source":"USPSTF; ACC/AHA"},
    {"test":"Vitamin B12","why":"Long-term metformin, with nerve symptoms on record","source":"ADA Standards of Care"}],
  "unlocks":["Kidney function monitoring","Cholesterol monitoring","More potassium from food","Salt substitute","Higher-protein plan"],
  "entry_methods":["Import a PDF or photo of the lab report","Type values from the report, with the report image attached","Patient-portal import (planned)"]},
 "nutrition":{"date":ds(today),
  "meals":[{"time":"07:30","label":"Oatmeal with blueberries, coffee","method":"Search"},{"time":"12:15","label":"Turkey sandwich on whole wheat, apple","method":"Barcode"},{"time":"18:40","label":"Grilled chicken, rice, green beans","method":"Photo, confirmed by you"}],
  "totals":[{"nutrient":"Sodium","range":"1,900–2,300 mg","target":"Under 2,300 mg","state":"WITHIN"},{"nutrient":"Fiber","range":"18–22 g","target":"About 30 g","state":"BELOW"},{"nutrient":"Potassium","range":"2,400–2,900 mg","target":"Held — see suggestions","state":"HELD"},{"nutrient":"Protein","range":"85–100 g","target":"Within general guidance","state":"WITHIN"},{"nutrient":"Calories","range":"1,850–2,150","target":"No target set","state":"INFO"}],
  "interaction_checks":[{"pair":"Alcohol and metformin","state":"NOTED","text":"Noted on the metformin label. No alcohol logged this week."},{"pair":"Potassium foods and lisinopril","state":"GATED","text":"Potassium suggestions are held."}],
  "excluded_note":"Alkaline-diet, detox and pH-balancing programs aren't part of PAi Health because they lack an evidence base."},
 "vitals":{"bp":bp,"daily":daily,"glucose":glu,"lisinopril_start":ds(lis)},
 "audit":[
  {"at":"2026-09-01 07:12","event":"Check-in prompt shown: blood pressure 14-day average above 140/90","rule":"EW-BP-02 v1"},
  {"at":"2026-09-08 16:40","event":"Medication start logged: lisinopril 10 mg","rule":"user entry"},
  {"at":"2026-09-10 08:03","event":"Suggestion adopted: lower sodium","rule":"user entry"},
  {"at":"2026-09-15 19:21","event":"Suggestion adopted: aerobic activity","rule":"user entry"},
  {"at":"2026-09-29 09:00","event":"Albuterol response pattern qualified from 21 logged doses","rule":"MV-LEARN v1"},
  {"at":"2026-10-01 09:00","event":"Annual labs marked overdue","rule":"LAB-ANNUAL v1"}]
}
json.dump(demo,open("src/data/tracker_demo.json","w"),indent=1,ensure_ascii=False)
print(len(bp),len(pre),len(post),demo["change_windows"][0]["pre"],demo["change_windows"][0]["post"],gh,wk,avg(recent,'systolic'),avg(recent,'diastolic'))
