import os,unittest
from decimal import Decimal
from unittest.mock import patch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker,create_async_engine
from apps.api.dependencies import AuthContext
from apps.api.architecture_pack_router import InquiryIn,LineIn,LocationIn,POIn,POLineIn,ProductIn,QuoteIn,ReceiveIn,RevisionIn,StockIn,SupplierIn,TransitionIn,create_location,create_po,create_product,create_quote,create_supplier,customer_decision,customer_quote,move_stock,public_inquiry,quote_transition,receive_po,revise_quote,transition_po
from packages.software_projects.planning.plan_service import approve,create_plan
from packages.software_projects.planning.service import apply_visual_change,create_project_from_plan,propose_visual_change,rollback_visual_change,ConflictError
from packages.database.db import Base
from packages.database.models import AppUser,Tenant,TenantMember
from packages.database.architecture_pack_models import Inquiry,PurchaseOrderLine,StockLevel
from packages.database import models,custom_software_models,architecture_pack_models

class PackWorkflowTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.engine=create_async_engine("sqlite+aiosqlite:///:memory:")
  async with self.engine.begin() as c:await c.run_sync(Base.metadata.create_all)
  self.sessions=async_sessionmaker(self.engine,expire_on_commit=False)
  async with self.sessions() as db:
   self.tenant=Tenant(name="Pack",slug="pack");self.user=AppUser(email="pack@test",password_hash="x",display_name="Owner");db.add_all([self.tenant,self.user]);await db.flush();db.add(TenantMember(tenant_id=self.tenant.id,user_id=self.user.id,role="owner"));await db.commit()
  self.auth=AuthContext(self.user,self.tenant,"owner")
 async def asyncTearDown(self):await self.engine.dispose()
 async def project(self,db,prompt):
  row,_,plan=await create_plan(db,self.tenant.id,self.user.id,prompt);await approve(db,row,1);return await create_project_from_plan(db,self.tenant.id,self.user.id,row,plan)
 async def test_complete_quotation_loop(self):
  with patch.dict(os.environ,{"SESSION_SECRET":"pack-test-secret-long-enough"}):
   async with self.sessions() as db:
    project=await self.project(db,"Build a travel agency quotation system where managers approve itineraries")
    first=await public_inquiry(project.slug,InquiryIn(name="Avery",email="a@example.com",requirements="Seven nights in Japan with rail travel",idempotencyKey="inquiry-key-001"),db);again=await public_inquiry(project.slug,InquiryIn(name="Avery",email="a@example.com",requirements="Seven nights in Japan with rail travel",idempotencyKey="inquiry-key-001"),db);self.assertFalse(again["created"])
    quote=await create_quote(QuoteIn(inquiryId=first["id"],deliverable="Tokyo and Kyoto itinerary",lines=[LineIn(description="Hotels",quantity=7,unitPrice=Decimal("200")),LineIn(description="Rail pass",quantity=1,unitPrice=Decimal("450"))]),self.auth,db);self.assertEqual(quote["total"],"1850")
    q=await quote_transition(quote["id"],TransitionIn(status="internal_review",expectedVersion=1),self.auth,db);q=await quote_transition(quote["id"],TransitionIn(status="approved_for_sending",expectedVersion=q["version"]),self.auth,db);q=await quote_transition(quote["id"],TransitionIn(status="sent_to_customer",expectedVersion=q["version"]),self.auth,db)
    view=await customer_quote(q["customerToken"],db);self.assertEqual(view["total"],"1850.00");decision=await customer_decision(q["customerToken"],TransitionIn(status="revision_requested",expectedVersion=view["version"]),db)
    revised=await revise_quote(quote["id"],RevisionIn(deliverable="Revised itinerary",lines=[LineIn(description="Hotels",quantity=7,unitPrice=Decimal("180"))],expectedVersion=decision["version"]),self.auth,db);sent=await quote_transition(quote["id"],TransitionIn(status="sent_to_customer",expectedVersion=revised["version"]),self.auth,db);accepted=await customer_decision(sent["customerToken"],TransitionIn(status="accepted",expectedVersion=sent["version"]),db);self.assertEqual(accepted["status"],"accepted");self.assertEqual((await customer_quote(sent["customerToken"],db))["quotationVersion"],2)
 async def test_inventory_receiving_and_order_loop(self):
  async with self.sessions() as db:
   project=await self.project(db,"Build grocery inventory with suppliers receiving low stock and purchase orders")
   location=await create_location(project.id,LocationIn(name="Main store"),self.auth,db);supplier=await create_supplier(project.id,SupplierIn(name="Fresh Foods",email=None),self.auth,db);product=await create_product(project.id,ProductIn(sku="APL",name="Apples",unitCost=Decimal("2"),reorderPoint=5),self.auth,db)
   stock=await move_stock(StockIn(productId=product["id"],locationId=location["id"],quantity=10,movementType="initial",reference="opening"),self.auth,db);stock=await move_stock(StockIn(productId=product["id"],locationId=location["id"],quantity=-7,movementType="adjustment",expectedVersion=stock["version"],reference="count"),self.auth,db);self.assertEqual(stock["quantity"],3)
   order=await create_po(project.id,POIn(supplierId=supplier["id"],lines=[POLineIn(productId=product["id"],quantity=10,unitCost=Decimal("2"))]),self.auth,db);order=await transition_po(order["id"],TransitionIn(status="approved",expectedVersion=1),self.auth,db);order=await transition_po(order["id"],TransitionIn(status="ordered",expectedVersion=order["version"]),self.auth,db);line=await db.scalar(select(PurchaseOrderLine).where(PurchaseOrderLine.purchase_order_id==order["id"]))
   partial=await receive_po(order["id"],ReceiveIn(locationId=location["id"],receipts={line.id:4},expectedVersion=order["version"]),self.auth,db);self.assertEqual(partial["status"],"partially_received");final=await receive_po(order["id"],ReceiveIn(locationId=location["id"],receipts={line.id:6},expectedVersion=partial["version"]),self.auth,db);self.assertEqual(final["status"],"received");closed=await transition_po(order["id"],TransitionIn(status="closed",expectedVersion=final["version"]),self.auth,db);self.assertEqual(closed["status"],"closed");level=await db.scalar(select(StockLevel).where(StockLevel.product_id==product["id"]));self.assertEqual(level.quantity,13)
 async def test_behavioral_edits_are_versioned_atomic_and_reversible(self):
  async with self.sessions() as db:
   quote=await self.project(db,"Build a travel quotation system with manager approval")
   change=await propose_visual_change(db,quote,self.user.id,"Move the quotation total into a right sidebar, hide internal margin from customers, and require manager approval before sending.",["customer_quotation.price_sidebar"],"desktop");self.assertIn("workflow.quotation.send_guard",change.impact_json);quote=await apply_visual_change(db,quote,change);self.assertEqual(quote.version,2)
   with self.assertRaises(ConflictError):await apply_visual_change(db,quote,change)
   quote=await rollback_visual_change(db,quote,change);self.assertEqual(quote.version,3)
   inventory=await self.project(db,"Build grocery inventory with suppliers stock receiving and purchase orders")
   edit=await propose_visual_change(db,inventory,self.user.id,"Turn this into a compact priority board, show the preferred supplier, and require manager approval for purchase orders above 5,000.",["inventory_dashboard.low_stock_queue"],"desktop");self.assertIn("workflow.purchase_order.approval_guard",edit.impact_json);inventory=await apply_visual_change(db,inventory,edit);self.assertEqual(inventory.version,2)
