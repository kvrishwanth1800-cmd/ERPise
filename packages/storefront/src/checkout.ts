export interface StorefrontProduct {
  readonly productId: string;
  readonly name: string;
  readonly price: number;
  readonly availableQuantity: number;
}

export interface CheckoutItem {
  readonly productId: string;
  readonly quantity: number;
}

export interface CheckoutCommand {
  readonly shopperId: string;
  readonly idempotencyKey: string;
  readonly items: readonly CheckoutItem[];
}

export type CheckoutOutcome =
  | {
      readonly status: 'completed';
      readonly orderId: string;
      readonly paymentId: string;
    }
  | {
      readonly status: 'payment_failed';
      readonly recoveryMessage: string;
    };

export interface CheckoutPort {
  submit(command: CheckoutCommand): Promise<CheckoutOutcome>;
}

export interface ProductPresentation {
  readonly heading: string;
  readonly priceText: string;
  readonly availabilityText: string;
}

export class StorefrontCheckout {
  private readonly requests = new Map<string, Promise<CheckoutOutcome>>();

  public constructor(private readonly checkoutPort: CheckoutPort) {}

  public presentProduct(product: StorefrontProduct): ProductPresentation {
    return {
      heading: product.name,
      priceText: `$${product.price.toFixed(2)}`,
      availabilityText:
        product.availableQuantity > 0 ? `${product.availableQuantity} available` : 'Out of stock'
    };
  }

  public checkout(command: CheckoutCommand): Promise<CheckoutOutcome> {
    this.validate(command);
    const existing = this.requests.get(command.idempotencyKey);
    if (existing !== undefined) {
      return existing;
    }

    const request = this.checkoutPort.submit(command);
    this.requests.set(command.idempotencyKey, request);
    return request;
  }

  private validate(command: CheckoutCommand): void {
    if (!command.shopperId || !command.idempotencyKey || command.items.length === 0) {
      throw new Error('Checkout requires shopper, idempotency key, and at least one item.');
    }
    if (command.items.some((item) => !item.productId || item.quantity <= 0)) {
      throw new Error('Checkout items require a product and positive quantity.');
    }
  }
}
