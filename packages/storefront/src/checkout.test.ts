import { describe, expect, it } from 'vitest';

import { type CheckoutCommand, type CheckoutOutcome, StorefrontCheckout } from './checkout.js';

const command: CheckoutCommand = {
  shopperId: 'shopper-1',
  idempotencyKey: 'checkout-1',
  items: [{ productId: 'product-1', quantity: 1 }]
};

describe('StorefrontCheckout', () => {
  it('presents product price and availability as semantic checkout content', () => {
    const checkout = new StorefrontCheckout({ submit: async () => completedOutcome() });

    expect(
      checkout.presentProduct({
        productId: 'product-1',
        name: 'Coffee',
        price: 3.5,
        availableQuantity: 1
      })
    ).toEqual({
      heading: 'Coffee',
      priceText: '$3.50',
      availabilityText: '1 available'
    });
  });

  it('uses one shared order request for retry attempts after a payment failure', async () => {
    let submissions = 0;
    const checkout = new StorefrontCheckout({
      submit: async () => {
        submissions += 1;
        return { status: 'payment_failed', recoveryMessage: 'Use another payment method.' };
      }
    });

    const first = checkout.checkout(command);
    const retry = checkout.checkout(command);

    await expect(first).resolves.toMatchObject({ status: 'payment_failed' });
    await expect(retry).resolves.toMatchObject({ status: 'payment_failed' });
    expect(submissions).toBe(1);
  });

  it('sends competing final-unit storefront attempts through the shared checkout port', async () => {
    let remainingQuantity = 1;
    const checkout = new StorefrontCheckout({
      submit: async (request) => {
        if (remainingQuantity < request.items[0]!.quantity) {
          return { status: 'payment_failed', recoveryMessage: 'Item is no longer available.' };
        }
        remainingQuantity -= request.items[0]!.quantity;
        return completedOutcome();
      }
    });

    const first = checkout.checkout(command);
    const second = checkout.checkout({ ...command, idempotencyKey: 'checkout-2' });
    const outcomes = await Promise.all([first, second]);

    expect(outcomes.filter((outcome) => outcome.status === 'completed')).toHaveLength(1);
  });
});

function completedOutcome(): CheckoutOutcome {
  return { status: 'completed', orderId: 'order-1', paymentId: 'payment-1' };
}
