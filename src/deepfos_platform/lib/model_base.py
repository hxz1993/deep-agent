from .pydantic_v1 import BaseModel as PydanticBaseModel, Field


class BaseModel(PydanticBaseModel):
    class Config:
        arbitrary_types_allowed = True

    @classmethod
    def normalize_path(cls, v):
        if v is None:
            return v
        return v.replace("\\", "/")


class SpaceAndApp(BaseModel):
    app: str = Field(..., description="应用id")
    space: str = Field(..., description="空间id")


class HeaderModel(SpaceAndApp):
    user: str = Field(..., description="用户id")
    language: str = Field(None, description="语言")
    token: str = Field(None, description="签名")
    cookie: str = Field(None, description="cookie")
    extra_headers: dict = Field(default_factory=dict)

    def dict(self, *arg, **kwargs):
        kwargs.setdefault("exclude_none", True)
        dct = super().dict(*arg, **kwargs)
        if extra := dct.pop("extra_headers", {}):
            dct.update(extra)
        return dct
