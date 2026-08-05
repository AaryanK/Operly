"""Operational APIs for distinct quotation and inventory architecture packs."""
import secrets
import os
import json
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from itsdangerous import BadSignature,SignatureExpired,URLSafeTimedSerializer
from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.database.architecture_pack_models import Inquiry, InventoryLocation, Product, PurchaseOrder, PurchaseOrderLine, Quotation, QuotationApproval, QuotationLineItem, QuotationStatusEvent, QuotationVersion, QuoteCustomer, StockLevel, StockMovement, Supplier
from packages.database.custom_software_models import GeneratedProject

router=APIRouter(tags=["architecture-packs"])
class Strict(BaseModel):model_config=ConfigDict(extra="forbid")
class InquiryIn(Strict):name:str=Field(min_length=2,max_length=160);email:str=Field(min_length=5,max_length=320);requirements:str=Field(min_length=10,max_length=4000);idempotencyKey:str=Field(min_length=8,max_length=120)
class LineIn(Strict):description:str=Field(min_length=1,max_length=500);quantity:int=Field(gt=0,le=10000);unitPrice:Decimal=Field(ge=0,max_digits=12,decimal_places=2)
class QuoteIn(Strict):inquiryId:str;deliverable:str=Field(default="",max_length=8000);lines:list[LineIn]=Field(min_length=1,max_length=100)
class TransitionIn(Strict):status:str;expectedVersion:int=Field(ge=1)
class ProductIn(Strict):sku:str=Field(min_length=1,max_length=80);name:str=Field(min_length=1,max_length=200);unitCost:Decimal=Field(ge=0);reorderPoint:int=Field(ge=0)
class StockIn(Strict):
    productId:str;locationId:str;quantity:int;movementType:str=Field(pattern="^(initial|receiving|sale_or_issue|adjustment|transfer|return)$");expectedVersion:int|None=None;reference:str=Field(default="",max_length=120)
    @field_validator("quantity")
    @classmethod
    def nonzero(cls,value):
        if value==0:raise ValueError("Quantity must not be zero")
        return value
class SupplierIn(Strict):name:str;email:str|None=None
class LocationIn(Strict):name:str
class POLineIn(Strict):productId:str;quantity:int=Field(gt=0,le=100000);unitCost:Decimal=Field(ge=0,max_digits=12,decimal_places=2)
class POIn(Strict):supplierId:str;lines:list[POLineIn]=Field(min_length=1,max_length=100)
class ReceiveIn(Strict):locationId:str;receipts:dict[str,int];expectedVersion:int
class RevisionIn(Strict):deliverable:str=Field(default="",max_length=8000);lines:list[LineIn]=Field(min_length=1,max_length=100);expectedVersion:int

def require(auth,roles):
    if auth.role not in roles:raise HTTPException(403,"Architecture-pack permission required")
def quote_token(quote_id):return URLSafeTimedSerializer(os.environ["SESSION_SECRET"],salt="customer-quotation").dumps(quote_id)
def quote_token_id(token):
    try:return URLSafeTimedSerializer(os.environ["SESSION_SECRET"],salt="customer-quotation").loads(token,max_age=60*60*24*14)
    except (BadSignature,SignatureExpired) as error:raise HTTPException(404,"Quotation link not found or expired") from error

@router.post("/api/public/quotation/{slug}/inquiries")
async def public_inquiry(slug:str,payload:InquiryIn,db:AsyncSession=Depends(get_db)):
    project=await db.scalar(select(GeneratedProject).where(GeneratedProject.slug==slug,GeneratedProject.architecture_pack=="quotation"))
    if not project:raise HTTPException(404,"Quotation project not found")
    tenant_id=project.tenant_id;existing=await db.scalar(select(Inquiry).where(Inquiry.tenant_id==tenant_id,Inquiry.project_id==project.id,Inquiry.idempotency_key==payload.idempotencyKey))
    if existing:return {"id":existing.id,"reference":existing.reference,"created":False}
    customer=QuoteCustomer(tenant_id=tenant_id,project_id=project.id,name=payload.name.strip(),email=payload.email.strip().lower());db.add(customer);await db.flush();row=Inquiry(tenant_id=tenant_id,project_id=project.id,customer_id=customer.id,reference=f"QI-{secrets.token_hex(3).upper()}",idempotency_key=payload.idempotencyKey,requirements=payload.requirements.strip());db.add(row);await db.commit();await db.refresh(row);return {"id":row.id,"reference":row.reference,"status":row.status,"created":True}

@router.get("/api/quotation/projects/{project_id}/inquiries")
async def quote_inquiries(project_id:str,limit:int=Query(50,ge=1,le=100),auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    project=await db.get(GeneratedProject,project_id)
    if not project or project.tenant_id!=auth.tenant.id or project.architecture_pack!="quotation":raise HTTPException(404,"Quotation project not found")
    rows=(await db.scalars(select(Inquiry).where(Inquiry.tenant_id==auth.tenant.id,Inquiry.project_id==project_id).order_by(Inquiry.created_at.desc()).limit(limit))).all();return [{"id":x.id,"reference":x.reference,"status":x.status,"requirements":x.requirements,"version":x.version} for x in rows]

@router.post("/api/quotation/quotations")
async def create_quote(payload:QuoteIn,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    require(auth,{"owner","manager","agent"});inquiry=await db.get(Inquiry,payload.inquiryId)
    if not inquiry or inquiry.tenant_id!=auth.tenant.id:raise HTTPException(404,"Inquiry not found")
    quote=Quotation(tenant_id=auth.tenant.id,project_id=inquiry.project_id,inquiry_id=inquiry.id);db.add(quote);await db.flush();total=sum(x.quantity*x.unitPrice for x in payload.lines);version=QuotationVersion(tenant_id=auth.tenant.id,quotation_id=quote.id,number=1,deliverable=payload.deliverable,total=total);db.add(version);await db.flush()
    for x in payload.lines:db.add(QuotationLineItem(tenant_id=auth.tenant.id,version_id=version.id,description=x.description,quantity=x.quantity,unit_price=x.unitPrice))
    db.add(QuotationStatusEvent(tenant_id=auth.tenant.id,quotation_id=quote.id,to_status="quotation_draft",actor_id=auth.user.id));await db.commit();return {"id":quote.id,"status":quote.status,"total":str(total),"version":quote.version}

QUOTE_TRANSITIONS={"quotation_draft":{"internal_review"},"internal_review":{"approved_for_sending"},"approved_for_sending":{"sent_to_customer"},"sent_to_customer":{"revision_requested","accepted","rejected"},"revision_requested":{"revised"},"revised":{"sent_to_customer"}}
@router.post("/api/quotation/quotations/{quote_id}/transition")
async def quote_transition(quote_id:str,payload:TransitionIn,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    quote=await db.get(Quotation,quote_id)
    if not quote or quote.tenant_id!=auth.tenant.id:raise HTTPException(404,"Quotation not found")
    if quote.version!=payload.expectedVersion:raise HTTPException(409,"Quotation changed; refresh")
    if payload.status not in QUOTE_TRANSITIONS.get(quote.status,set()):raise HTTPException(422,"Invalid quotation transition")
    if payload.status=="approved_for_sending":require(auth,{"owner","manager"});db.add(QuotationApproval(tenant_id=auth.tenant.id,quotation_id=quote.id,status="approved",actor_id=auth.user.id))
    if payload.status=="sent_to_customer" and not await db.scalar(select(QuotationApproval.id).where(QuotationApproval.quotation_id==quote.id,QuotationApproval.status=="approved")):raise HTTPException(422,"Manager approval is required before sending")
    old=quote.status;quote.status=payload.status;quote.version+=1
    if payload.status=="sent_to_customer" and not quote.public_token:quote.public_token=quote_token(quote.id)
    db.add(QuotationStatusEvent(tenant_id=auth.tenant.id,quotation_id=quote.id,from_status=old,to_status=quote.status,actor_id=auth.user.id));await db.commit();return {"id":quote.id,"status":quote.status,"version":quote.version,"customerToken":quote.public_token}

@router.get("/api/quotation/quotations/{quote_id}")
async def quote_detail(quote_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    quote=await db.get(Quotation,quote_id)
    if not quote or quote.tenant_id!=auth.tenant.id:raise HTTPException(404,"Quotation not found")
    versions=(await db.scalars(select(QuotationVersion).where(QuotationVersion.quotation_id==quote.id).order_by(QuotationVersion.number))).all()
    return {"id":quote.id,"status":quote.status,"version":quote.version,"versions":[{"id":v.id,"number":v.number,"deliverable":v.deliverable,"total":str(v.total)} for v in versions],"customerUrl":f"/quotation/customer/{quote.public_token}" if quote.public_token else None}

@router.get("/api/quotation/projects/{project_id}/quotations")
async def project_quotes(project_id:str,limit:int=Query(50,ge=1,le=100),auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    project=await db.get(GeneratedProject,project_id)
    if not project or project.tenant_id!=auth.tenant.id or project.architecture_pack!="quotation":raise HTTPException(404,"Quotation project not found")
    rows=(await db.scalars(select(Quotation).where(Quotation.tenant_id==auth.tenant.id,Quotation.project_id==project_id).order_by(Quotation.created_at.desc()).limit(limit))).all();out=[]
    for quote in rows:
        version=await db.scalar(select(QuotationVersion).where(QuotationVersion.quotation_id==quote.id,QuotationVersion.number==quote.current_version));out.append({"id":quote.id,"inquiryId":quote.inquiry_id,"status":quote.status,"version":quote.version,"quotationVersion":quote.current_version,"total":str(version.total),"customerUrl":f"/quotation/customer/{quote.public_token}" if quote.public_token else None})
    return out

@router.post("/api/quotation/quotations/{quote_id}/revisions")
async def revise_quote(quote_id:str,payload:RevisionIn,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    require(auth,{"owner","manager","agent"});quote=await db.get(Quotation,quote_id)
    if not quote or quote.tenant_id!=auth.tenant.id:raise HTTPException(404,"Quotation not found")
    if quote.version!=payload.expectedVersion:raise HTTPException(409,"Quotation changed; refresh")
    if quote.status!="revision_requested":raise HTTPException(422,"A revision can only follow a customer revision request")
    number=quote.current_version+1;total=sum(x.quantity*x.unitPrice for x in payload.lines);version=QuotationVersion(tenant_id=quote.tenant_id,quotation_id=quote.id,number=number,deliverable=payload.deliverable,total=total);db.add(version);await db.flush()
    for x in payload.lines:db.add(QuotationLineItem(tenant_id=quote.tenant_id,version_id=version.id,description=x.description,quantity=x.quantity,unit_price=x.unitPrice))
    quote.current_version=number;old=quote.status;quote.status="revised";quote.version+=1;db.add(QuotationStatusEvent(tenant_id=quote.tenant_id,quotation_id=quote.id,from_status=old,to_status="revised",actor_id=auth.user.id));await db.commit();return {"id":quote.id,"status":quote.status,"version":quote.version,"quotationVersion":number,"total":str(total)}

@router.get("/api/public/quotation/customer/{token}")
async def customer_quote(token:str,db:AsyncSession=Depends(get_db)):
    quote=await db.get(Quotation,quote_token_id(token))
    if not quote or quote.public_token!=token or quote.status not in {"sent_to_customer","revision_requested","accepted","rejected"}:raise HTTPException(404,"Quotation not found")
    version=await db.scalar(select(QuotationVersion).where(QuotationVersion.quotation_id==quote.id,QuotationVersion.number==quote.current_version));lines=(await db.scalars(select(QuotationLineItem).where(QuotationLineItem.version_id==version.id))).all();events=(await db.scalars(select(QuotationStatusEvent).where(QuotationStatusEvent.quotation_id==quote.id).order_by(QuotationStatusEvent.created_at))).all()
    return {"id":quote.id,"status":quote.status,"version":quote.version,"quotationVersion":version.number,"deliverable":version.deliverable,"total":str(version.total),"lines":[{"description":x.description,"quantity":x.quantity,"unitPrice":str(x.unit_price),"total":str(x.quantity*x.unit_price)} for x in lines],"history":[x.to_status for x in events]}

@router.post("/api/public/quotation/customer/{token}/decision")
async def customer_decision(token:str,payload:TransitionIn,db:AsyncSession=Depends(get_db)):
    quote=await db.get(Quotation,quote_token_id(token))
    if not quote or quote.public_token!=token:raise HTTPException(404,"Quotation not found")
    if quote.version!=payload.expectedVersion:raise HTTPException(409,"Quotation changed; refresh")
    if quote.status!="sent_to_customer" or payload.status not in {"revision_requested","accepted","rejected"}:raise HTTPException(422,"Invalid customer decision")
    old=quote.status;quote.status=payload.status;quote.version+=1;db.add(QuotationStatusEvent(tenant_id=quote.tenant_id,quotation_id=quote.id,from_status=old,to_status=quote.status,actor_id=None));await db.commit();return {"id":quote.id,"status":quote.status,"version":quote.version}

@router.post("/api/inventory/projects/{project_id}/locations")
async def create_location(project_id:str,payload:LocationIn,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    require(auth,{"owner","manager"});row=InventoryLocation(tenant_id=auth.tenant.id,project_id=project_id,name=payload.name);db.add(row);await db.commit();await db.refresh(row);return {"id":row.id,"name":row.name}
@router.post("/api/inventory/projects/{project_id}/suppliers")
async def create_supplier(project_id:str,payload:SupplierIn,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    require(auth,{"owner","manager","purchasing_employee"});row=Supplier(tenant_id=auth.tenant.id,project_id=project_id,name=payload.name,email=payload.email);db.add(row);await db.commit();await db.refresh(row);return {"id":row.id,"name":row.name}
@router.post("/api/inventory/projects/{project_id}/products")
async def create_product(project_id:str,payload:ProductIn,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    require(auth,{"owner","manager","stock_employee"});row=Product(tenant_id=auth.tenant.id,project_id=project_id,sku=payload.sku,name=payload.name,unit_cost=payload.unitCost,reorder_point=payload.reorderPoint);db.add(row);await db.commit();await db.refresh(row);return {"id":row.id,"sku":row.sku,"name":row.name,"version":row.version}
@router.post("/api/inventory/stock-movements")
async def move_stock(payload:StockIn,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    require(auth,{"owner","manager","stock_employee"});product=await db.get(Product,payload.productId);location=await db.get(InventoryLocation,payload.locationId)
    if not product or not location or product.tenant_id!=auth.tenant.id or location.tenant_id!=auth.tenant.id:raise HTTPException(404,"Product or location not found")
    level=await db.scalar(select(StockLevel).where(StockLevel.tenant_id==auth.tenant.id,StockLevel.product_id==product.id,StockLevel.location_id==location.id))
    if not level:level=StockLevel(tenant_id=auth.tenant.id,product_id=product.id,location_id=location.id);db.add(level);await db.flush()
    if payload.expectedVersion is not None and level.version!=payload.expectedVersion:raise HTTPException(409,"Stock changed; refresh")
    if level.quantity+payload.quantity<0:raise HTTPException(422,"Stock quantity cannot become negative")
    level.quantity+=payload.quantity;level.version+=1;db.add(StockMovement(tenant_id=auth.tenant.id,product_id=product.id,location_id=location.id,movement_type=payload.movementType,quantity_delta=payload.quantity,reference=payload.reference,actor_id=auth.user.id));await db.commit();return {"productId":product.id,"locationId":location.id,"quantity":level.quantity,"version":level.version}
@router.get("/api/inventory/projects/{project_id}/low-stock")
async def low_stock(project_id:str,limit:int=Query(50,ge=1,le=100),auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    rows=(await db.execute(select(Product,func.coalesce(func.sum(StockLevel.quantity),0)).outerjoin(StockLevel,StockLevel.product_id==Product.id).where(Product.tenant_id==auth.tenant.id,Product.project_id==project_id).group_by(Product.id).having(func.coalesce(func.sum(StockLevel.quantity),0)<=Product.reorder_point).limit(limit))).all();return [{"productId":p.id,"sku":p.sku,"name":p.name,"quantity":int(q),"reorderPoint":p.reorder_point} for p,q in rows]

@router.get("/api/inventory/projects/{project_id}/products")
async def products(project_id:str,limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0,le=10000),auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    rows=(await db.scalars(select(Product).where(Product.tenant_id==auth.tenant.id,Product.project_id==project_id).order_by(Product.name).offset(offset).limit(limit))).all();return [{"id":x.id,"sku":x.sku,"name":x.name,"unitCost":str(x.unit_cost),"reorderPoint":x.reorder_point,"version":x.version} for x in rows]
@router.get("/api/inventory/projects/{project_id}/suppliers")
async def suppliers(project_id:str,limit:int=Query(50,ge=1,le=100),auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    rows=(await db.scalars(select(Supplier).where(Supplier.tenant_id==auth.tenant.id,Supplier.project_id==project_id).order_by(Supplier.name).limit(limit))).all();return [{"id":x.id,"name":x.name,"email":x.email} for x in rows]
@router.get("/api/inventory/projects/{project_id}/locations")
async def locations(project_id:str,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    rows=(await db.scalars(select(InventoryLocation).where(InventoryLocation.tenant_id==auth.tenant.id,InventoryLocation.project_id==project_id).order_by(InventoryLocation.name))).all();return [{"id":x.id,"name":x.name} for x in rows]
@router.get("/api/inventory/projects/{project_id}/purchase-orders")
async def purchase_orders(project_id:str,limit:int=Query(50,ge=1,le=100),auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    rows=(await db.scalars(select(PurchaseOrder).where(PurchaseOrder.tenant_id==auth.tenant.id,PurchaseOrder.project_id==project_id).order_by(PurchaseOrder.created_at.desc()).limit(limit))).all();out=[]
    for order in rows:
        lines=(await db.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.purchase_order_id==order.id))).all();out.append({"id":order.id,"supplierId":order.supplier_id,"status":order.status,"version":order.version,"total":str(sum(x.ordered_quantity*x.unit_cost for x in lines)),"lines":[{"id":x.id,"productId":x.product_id,"ordered":x.ordered_quantity,"received":x.received_quantity,"unitCost":str(x.unit_cost)} for x in lines]})
    return out

@router.get("/api/inventory/projects/{project_id}/movements")
async def movements(project_id:str,limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0,le=10000),auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    product_ids=select(Product.id).where(Product.tenant_id==auth.tenant.id,Product.project_id==project_id);rows=(await db.scalars(select(StockMovement).where(StockMovement.tenant_id==auth.tenant.id,StockMovement.product_id.in_(product_ids)).order_by(StockMovement.created_at.desc()).offset(offset).limit(limit))).all();return [{"id":x.id,"productId":x.product_id,"locationId":x.location_id,"type":x.movement_type,"quantity":x.quantity_delta,"reference":x.reference} for x in rows]

@router.post("/api/inventory/projects/{project_id}/purchase-orders")
async def create_po(project_id:str,payload:POIn,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    require(auth,{"owner","manager","purchasing_employee"});supplier=await db.get(Supplier,payload.supplierId)
    if not supplier or supplier.tenant_id!=auth.tenant.id or supplier.project_id!=project_id:raise HTTPException(404,"Supplier not found")
    order=PurchaseOrder(tenant_id=auth.tenant.id,project_id=project_id,supplier_id=supplier.id);db.add(order);await db.flush();total=Decimal("0")
    for item in payload.lines:
        product=await db.get(Product,item.productId)
        if not product or product.tenant_id!=auth.tenant.id or product.project_id!=project_id:raise HTTPException(404,"Product not found")
        db.add(PurchaseOrderLine(tenant_id=auth.tenant.id,purchase_order_id=order.id,product_id=product.id,ordered_quantity=item.quantity,unit_cost=item.unitCost));total+=item.quantity*item.unitCost
    await db.commit();return {"id":order.id,"status":order.status,"version":order.version,"total":str(total)}

PO_TRANSITIONS={"draft":{"approved"},"approved":{"ordered"},"received":{"closed"}}
@router.post("/api/inventory/purchase-orders/{order_id}/transition")
async def transition_po(order_id:str,payload:TransitionIn,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    order=await db.get(PurchaseOrder,order_id)
    if not order or order.tenant_id!=auth.tenant.id:raise HTTPException(404,"Purchase order not found")
    if order.version!=payload.expectedVersion:raise HTTPException(409,"Purchase order changed; refresh")
    allowed=set(PO_TRANSITIONS.get(order.status,set()))
    if order.status=="draft" and payload.status=="ordered":
        project=await db.get(GeneratedProject,order.project_id);config=json.loads(project.brand_json);threshold=config.get("purchaseOrderApprovalThreshold");lines=(await db.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.purchase_order_id==order.id))).all();total=sum(x.ordered_quantity*x.unit_cost for x in lines)
        if threshold is not None and total<=threshold:allowed.add("ordered")
        elif threshold is not None:raise HTTPException(422,"Manager approval is required for purchase orders above 5,000")
    if payload.status not in allowed:raise HTTPException(422,"Invalid purchase-order transition")
    if payload.status=="approved":require(auth,{"owner","manager"})
    order.status=payload.status;order.version+=1;await db.commit();return {"id":order.id,"status":order.status,"version":order.version}

@router.post("/api/inventory/purchase-orders/{order_id}/receive")
async def receive_po(order_id:str,payload:ReceiveIn,auth:AuthContext=Depends(get_auth_context),db:AsyncSession=Depends(get_db)):
    require(auth,{"owner","manager","stock_employee"});order=await db.get(PurchaseOrder,order_id);location=await db.get(InventoryLocation,payload.locationId)
    if not order or not location or order.tenant_id!=auth.tenant.id or location.tenant_id!=auth.tenant.id or location.project_id!=order.project_id:raise HTTPException(404,"Purchase order or location not found")
    if order.version!=payload.expectedVersion:raise HTTPException(409,"Purchase order changed; refresh")
    if order.status not in {"ordered","partially_received"}:raise HTTPException(422,"Purchase order is not receivable")
    lines=(await db.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.purchase_order_id==order.id))).all();by_id={x.id:x for x in lines}
    for line_id,quantity in payload.receipts.items():
        if line_id not in by_id or quantity<=0:raise HTTPException(422,"Invalid receipt line")
        line=by_id[line_id]
        if line.received_quantity+quantity>line.ordered_quantity:raise HTTPException(422,"Receipt exceeds ordered quantity")
        level=await db.scalar(select(StockLevel).where(StockLevel.tenant_id==auth.tenant.id,StockLevel.product_id==line.product_id,StockLevel.location_id==location.id))
        if not level:level=StockLevel(tenant_id=auth.tenant.id,product_id=line.product_id,location_id=location.id);db.add(level);await db.flush()
        line.received_quantity+=quantity;level.quantity+=quantity;level.version+=1;db.add(StockMovement(tenant_id=auth.tenant.id,product_id=line.product_id,location_id=location.id,movement_type="receiving",quantity_delta=quantity,reference=order.id,actor_id=auth.user.id))
    order.status="received" if all(x.received_quantity==x.ordered_quantity for x in lines) else "partially_received";order.version+=1;await db.commit();return {"id":order.id,"status":order.status,"version":order.version}
