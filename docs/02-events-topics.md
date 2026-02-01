# Events & Topics (v1)

## Topics and routing

| Topic | Produced by | Consumed by | Message key |
|---|---|---|---|
| `ecomm.cart.CartCheckedOut.v1` | cart-service | order-service | `cart_id` |
| `ecomm.orders.OrderCreated.v1` | order-service | payment-service | `order_id` |
| `ecomm.payment.PaymentAuthorized.v1` | payment-service | order-service | `order_id` |
| `ecomm.payment.PaymentFailed.v1` | payment-service | order-service | `order_id` |
| `ecomm.orders.OrderPaid.v1` | order-service | shipping-service | `order_id` |
| `ecomm.shipping.ShipmentCreated.v1` | shipping-service | optional (order-service / UI aggregator) | `order_id` |

## Notes
- Delivery is **at-least-once** (duplicates possible). Consumers must be **idempotent** (use `event_id`).
- Use Kafka message key = aggregate id (e.g., `order_id`) to keep ordering within a partition for each aggregate.
