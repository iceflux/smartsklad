"""Сортировка списка по колонке, выбранной в адресной строке.

Ключ сортировки приходит от человека, поэтому он не может быть строкой в SQL.
Здесь он **ищется в белом списке** и заменяется объектом колонки, то есть подставить
в запрос можно только то, что уже перечислено в словаре раздела. Неизвестный ключ
не отвергается, а заменяется ключом по умолчанию: сортировка — украшение, и из-за
опечатки в адресной строке страница обязана открыться, а не отдать 500.

Второй ключ — `id`, и он обязателен. Без него строки, равные по сортируемой
колонке, не имеют определённого порядка, и постраничный разбор начинает
дублировать и терять их: страница может отдать одну и ту же строку дважды, а
другую не отдать вовсе.

Колонки из таблиц связи («Клиент» у заказа, «Категория» и «Ед.» у товара)
сортируются не своими колонками, а присоединением. Присоединение внешнее и
делается по требованию: внутреннее убрало бы из списка товар без категории.
"""

from sqlalchemy import ColumnElement
from sqlalchemy.orm.decl_api import DeclarativeMeta


def _direction(column: ColumnElement, direction: str | None):
    return column.desc() if direction == "desc" else column.asc()


def apply_sort(query, model: DeclarativeMeta, sorts: dict, default_sort: str,
               default_direction: str, sort: str | None, direction: str | None,
               joins: dict | None = None):
    """Один вызов задаёт и сортировку, и обязательный второй ключ — иначе про
    `id` легко забыть, а забытый второй ключ не падает, он тихо ломает страницы.

    `joins` — для колонок, которых в своей таблице нет: ключ сортировки ведёт в
    таблицу связи, и её надо присоединить. Соединение **внешнее** и делается
    только для выбранного ключа: внутреннее выкинуло бы из списка товар без
    категории, а это выглядит как потеря данных, а `LEFT JOIN` оставляет такую
    строку на месте — с пустой категорией, то есть в начале при сортировке по
    возрастанию."""
    key = sort if sort in sorts else default_sort
    if joins and key in joins:
        target, onclause = joins[key]
        query = query.outerjoin(target, onclause)
    return query.order_by(
        _direction(sorts[key], direction if direction in ("asc", "desc") else default_direction),
        _direction(model.id, direction if direction in ("asc", "desc") else default_direction),
    )
