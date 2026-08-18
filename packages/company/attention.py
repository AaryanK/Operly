from datetime import datetime,timedelta
from sqlalchemy import func,select
from packages.database.business_models import Appointment,Lead,Quote
from packages.database.models import Approval,Task

async def attention_items(db,tenant_id,now=None,stale_days=7,horizon_days=7):
 now=now or datetime.utcnow();items=[]
 leads=(await db.scalars(select(Lead).where(Lead.tenant_id==tenant_id,Lead.stage.not_in(["won","lost"])))).all()
 stale=[x for x in leads if not x.next_action_at and (x.last_activity_at or x.last_contacted_at or x.stage_changed_at or x.created_at)<=now-timedelta(days=stale_days)]
 if stale:items.append({"type":"stale_leads","title":"Leads are going quiet","reason":f"{len(stale)} open lead(s) have no scheduled next action and no recent activity.","count":len(stale),"estimated_value":sum(x.value or 0 for x in stale) or None,"proposed_action":"Review and prepare follow-ups","cta":{"page":"customers","action":"review_stale_leads"}})
 overdue=[x for x in leads if x.next_action_at and x.next_action_at<now]
 if overdue:items.append({"type":"overdue_follow_up","title":"Follow-ups are overdue","reason":"Scheduled lead follow-ups are past due.","count":len(overdue),"estimated_value":sum(x.value or 0 for x in overdue) or None,"proposed_action":"Prepare overdue follow-ups","cta":{"page":"customers","action":"review_overdue"}})
 quotes=(await db.scalars(select(Quote).where(Quote.tenant_id==tenant_id,Quote.status.in_(["draft","sent","pending"]),Quote.valid_until>=now,Quote.valid_until<=now+timedelta(days=horizon_days)))).all()
 if quotes:items.append({"type":"expiring_quotes","title":"Quotes expire soon","reason":f"{len(quotes)} quote(s) expire within {horizon_days} days.","count":len(quotes),"estimated_value":sum(x.total or 0 for x in quotes) or None,"proposed_action":"Review expiring quotes","cta":{"page":"customers","action":"review_quotes"}})
 appointments=(await db.scalars(select(Appointment).where(Appointment.tenant_id==tenant_id,Appointment.status=="scheduled",Appointment.starts_at>=now,Appointment.starts_at<=now+timedelta(days=horizon_days)))).all()
 if appointments:items.append({"type":"upcoming_appointments","title":"Appointments coming up","reason":f"{len(appointments)} appointment(s) are scheduled in the next {horizon_days} days.","count":len(appointments),"estimated_value":None,"proposed_action":"Review the schedule","cta":{"page":"operate","action":"view_calendar"}})
 pending=await db.scalar(select(func.count(Approval.id)).where(Approval.tenant_id==tenant_id,Approval.status=="pending")) or 0
 if pending:items.append({"type":"pending_approvals","title":"Actions need your approval","reason":"Operly is waiting before taking consequential action.","count":pending,"estimated_value":None,"proposed_action":"Review exact actions","cta":{"page":"home","action":"review_approvals"}})
 return items
