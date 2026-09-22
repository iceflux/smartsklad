import enum


class Permission(str, enum.Enum):
    products_view = "products.view"
    products_edit = "products.edit"
    stock_change = "stock.change"
    price_change = "price.change"
    categories_manage = "categories.manage"
    orders_view = "orders.view"
    orders_create = "orders.create"
    orders_edit = "orders.edit"
    orders_delete = "orders.delete"
    orders_status = "orders.status"
    customers_manage = "customers.manage"
    analytics_view = "analytics.view"
    reports_export = "reports.export"
    users_manage = "users.manage"
    backup_manage = "backup.manage"
    audit_view = "audit.view"
    settings_change = "settings.change"
    notifications_send = "notifications.send"
    notifications_view = "notifications.view"