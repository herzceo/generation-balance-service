from typing import TYPE_CHECKING, Any

from sqlalchemy import MetaData
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.orm import DeclarativeBase

from backend.internal.case import pascal_case_to_snake_case

convention = {
    "ix": "ix_%(table_name)s_%(column_0_N_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    __abstract__: bool = True
    metadata = MetaData(naming_convention=convention)

    if TYPE_CHECKING:
        id: Any

    @declared_attr.directive
    def __tablename__(self) -> str:
        return pascal_case_to_snake_case(self.__name__)

    def to_builtins(self) -> dict[str, Any]:
        d = {}
        for column in self.__table__.columns:
            value = getattr(self, column.name)
            if value is None:
                if column.server_default is not None:
                    continue
                if column.autoincrement is True:
                    continue
                if column.default is not None:
                    if column.default.is_callable:
                        value = column.default.arg(None)
                    else:
                        value = column.default.arg
            d[column.name] = value
        return d
