from typing import Any

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

    @declared_attr.directive
    def __tablename__(self) -> str:
        return pascal_case_to_snake_case(self.__name__)

    def to_builtins(self) -> dict[str, Any]:
        values = {column.name: getattr(self, column.name) for column in self.__table__.columns}
        return {
            name: value
            for name, value in values.items()
            if value is not None or self.__table__.columns[name].server_default is None
        }
