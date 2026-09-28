from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import SessionLocal
from app.core.security import get_current_user
from app.models.cliente import Cliente
from app.schemas.cliente import ClienteCreate, ClienteDetalleOut, ClienteOut, ClienteUpdate
from app.services.auditoria_service import construir_snapshot, registrar_auditoria

router = APIRouter(tags=["clientes"], dependencies=[Depends(get_current_user)])


@router.get("", response_model=list[ClienteOut])
async def listar_clientes(
    nombre: str | None = Query(None),
    telefono: str | None = Query(None),
):
    async with SessionLocal() as session:
        query = select(Cliente)

        if nombre is not None:
            query = query.where(Cliente.nombre.ilike(f"%{nombre}%"))
        if telefono is not None:
            query = query.where(Cliente.telefono.ilike(f"%{telefono}%"))

        result = await session.execute(query)
        return result.scalars().all()


@router.post("", response_model=ClienteOut, status_code=status.HTTP_201_CREATED)
async def crear_cliente(datos: ClienteCreate, current_user: dict = Depends(get_current_user)):
    async with SessionLocal() as session:
        result = await session.execute(
            select(Cliente).where(Cliente.telefono == datos.telefono)
        )
        if result.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ya existe un cliente con ese teléfono",
            )

        cliente = Cliente(**datos.model_dump())
        session.add(cliente)
        await session.commit()
        await session.refresh(cliente)

        await registrar_auditoria(
            usuario=current_user.get("email"),
            entidad="cliente",
            entidad_id=cliente.id,
            accion="crear",
            antes=None,
            despues=construir_snapshot(cliente),
        )

        return cliente


@router.get("/{id}", response_model=ClienteDetalleOut)
async def obtener_cliente(id: int):
    async with SessionLocal() as session:
        result = await session.execute(
            select(Cliente)
            .where(Cliente.id == id)
            .options(selectinload(Cliente.pedidos))
        )
        cliente = result.scalar_one_or_none()
        if cliente is None:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")

        cliente.pedidos.sort(key=lambda pedido: pedido.creado_en, reverse=True)

        return cliente


@router.patch("/{id}", response_model=ClienteOut)
async def actualizar_cliente(
    id: int, datos: ClienteUpdate, current_user: dict = Depends(get_current_user)
):
    async with SessionLocal() as session:
        cliente = await session.get(Cliente, id)
        if cliente is None:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")

        snapshot_antes = construir_snapshot(cliente)

        for campo, valor in datos.model_dump(exclude_unset=True).items():
            setattr(cliente, campo, valor)

        await session.commit()
        await session.refresh(cliente)

        await registrar_auditoria(
            usuario=current_user.get("email"),
            entidad="cliente",
            entidad_id=cliente.id,
            accion="actualizar",
            antes=snapshot_antes,
            despues=construir_snapshot(cliente),
        )

        return cliente
