from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from deep_agent.agent.auxiliary_models import AUXILIARY_MODEL_CATALOG, is_known_use_case
from deep_agent.db.deps import get_auxiliary_model_store, get_llm_model_store
from deep_agent.db.models import AuxiliaryModelConfig
from deep_agent.db.stores.auxiliary_model import AuxiliaryModelStoreBase
from deep_agent.db.stores.llm_model import LlmModelStoreBase
from deep_agent.schemas.auxiliary_model import AuxiliaryModelResponse, AuxiliaryModelUpdate

router = APIRouter()


def _response_for_use_case(
    use_case: str,
    *,
    llm_model_id: str | None,
    llm_model_name: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key_set: bool = False,
    updated_at=None,
) -> AuxiliaryModelResponse:
    meta = AUXILIARY_MODEL_CATALOG[use_case]
    return AuxiliaryModelResponse.from_catalog_and_row(
        meta,
        llm_model_id=llm_model_id,
        llm_model_name=llm_model_name,
        model=model,
        base_url=base_url,
        api_key_set=api_key_set,
        updated_at=updated_at,
    )


async def _response_for_row(
    use_case: str,
    row: AuxiliaryModelConfig | None,
    llm_model_store: LlmModelStoreBase,
) -> AuxiliaryModelResponse:
    meta = AUXILIARY_MODEL_CATALOG[use_case]
    if row is None or not row.llm_model_id:
        return AuxiliaryModelResponse.from_catalog_only(meta)

    llm_row = await llm_model_store.get_by_id(row.llm_model_id)
    if llm_row is None:
        return _response_for_use_case(
            use_case,
            llm_model_id=None,
            updated_at=row.updated_at,
        )
    return _response_for_use_case(
        use_case,
        llm_model_id=row.llm_model_id,
        llm_model_name=llm_row.name,
        model=llm_row.model,
        base_url=llm_row.base_url,
        api_key_set=bool(llm_row.api_key),
        updated_at=row.updated_at,
    )


@router.get("/auxiliary-models", response_model=list[AuxiliaryModelResponse])
async def list_auxiliary_models(
    auxiliary_store: AuxiliaryModelStoreBase = Depends(get_auxiliary_model_store),
    llm_model_store: LlmModelStoreBase = Depends(get_llm_model_store),
) -> list[AuxiliaryModelResponse]:
    await auxiliary_store.ensure_defaults()
    rows = {row.use_case: row for row in await auxiliary_store.list_all()}
    return [
        (await _response_for_row(use_case, rows.get(use_case), llm_model_store))
        for use_case in AUXILIARY_MODEL_CATALOG
    ]


@router.get("/auxiliary-models/{use_case}", response_model=AuxiliaryModelResponse)
async def get_auxiliary_model(
    use_case: str,
    auxiliary_store: AuxiliaryModelStoreBase = Depends(get_auxiliary_model_store),
    llm_model_store: LlmModelStoreBase = Depends(get_llm_model_store),
) -> AuxiliaryModelResponse:
    if not is_known_use_case(use_case):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown use case: {use_case}")
    await auxiliary_store.ensure_defaults()
    row = await auxiliary_store.get_by_use_case(use_case)
    return await _response_for_row(use_case, row, llm_model_store)


@router.patch("/auxiliary-models/{use_case}", response_model=AuxiliaryModelResponse)
async def update_auxiliary_model(
    use_case: str,
    body: AuxiliaryModelUpdate,
    auxiliary_store: AuxiliaryModelStoreBase = Depends(get_auxiliary_model_store),
    llm_model_store: LlmModelStoreBase = Depends(get_llm_model_store),
) -> AuxiliaryModelResponse:
    if not is_known_use_case(use_case):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown use case: {use_case}")

    if "llm_model_id" in body.model_fields_set and body.llm_model_id is not None:
        llm_row = await llm_model_store.get_by_id(body.llm_model_id)
        if llm_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"LLM model not found: {body.llm_model_id}",
            )

    await auxiliary_store.ensure_defaults()
    row = await auxiliary_store.update(use_case, body)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Use case not found: {use_case}")

    return await _response_for_row(use_case, row, llm_model_store)
