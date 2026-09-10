/**
 * Entry fee payment, through Razorpay Checkout.
 *
 * The browser's part of this is deliberately small. It does not know the
 * amount until the server tells it, it does not decide whether an entry is
 * paid, and it cannot confirm one: it opens Checkout with an order the server
 * created, and hands the result straight back for verification. Everything
 * that decides anything happens in `backend/app/routers/payments.py`.
 *
 * The key_id is fetched rather than built in, so switching a deployment from
 * test to live is a server environment change with no frontend rebuild -- and
 * so the bundle can never ship a live key by accident.
 */

import { apiClient } from '../utils/apiClient';
import { Registration } from '../types/tournament';

const CHECKOUT_SRC = 'https://checkout.razorpay.com/v1/checkout.js';

export interface PaymentConfig {
  enabled: boolean;
  keyId: string;
  liveMode: boolean;
}

export interface PaymentOrder {
  orderId: string;
  amount: number;          // paise
  currency: string;
  keyId: string;
  tournamentName: string;
  prefill: { name: string; email: string; contact: string };
}

export interface PaymentRecord {
  id: string;
  registrationId: string;
  razorpayOrderId: string;
  razorpayPaymentId?: string | null;
  amountPaise: number;
  amount: number;          // rupees, for display
  currency: string;
  status: 'created' | 'paid' | 'failed' | 'refunded';
  signatureVerified: boolean;
  confirmedVia?: 'callback' | 'webhook' | null;
  errorDescription?: string | null;
  method?: string | null;
  createdAt: string;
  paidAt?: string | null;
}

/** Razorpay Checkout's success payload. Sent back verbatim; see PaymentVerifySchema. */
interface CheckoutSuccess {
  razorpay_order_id: string;
  razorpay_payment_id: string;
  razorpay_signature: string;
}

/**
 * Raised when the player closed Checkout without paying.
 *
 * A distinct type because this is not a failure to report: they changed their
 * mind, the entry is still there unpaid, and showing them a red error for it
 * would be wrong. Callers catch this and say nothing.
 */
export class PaymentDismissedError extends Error {
  readonly dismissed = true;
  constructor() {
    super('Payment window closed before the payment was made.');
    this.name = 'PaymentDismissedError';
  }
}

let scriptPromise: Promise<void> | null = null;

/**
 * Load Checkout's script once.
 *
 * Cached as a promise rather than a boolean so that two components opening
 * checkout at the same moment wait on one load instead of injecting two tags.
 * A failed load clears the cache, so a player who was offline for a moment can
 * retry rather than being stuck with a permanently rejected promise.
 */
function loadCheckoutScript(): Promise<void> {
  if (typeof window === 'undefined') {
    return Promise.reject(new Error('Payments are only available in a browser.'));
  }
  if ((window as any).Razorpay) return Promise.resolve();
  if (scriptPromise) return scriptPromise;

  scriptPromise = new Promise<void>((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(`script[src="${CHECKOUT_SRC}"]`);
    const script = existing || document.createElement('script');

    script.addEventListener('load', () => resolve());
    script.addEventListener('error', () => {
      scriptPromise = null;
      reject(new Error('Could not load the payment window. Check your connection and try again.'));
    });

    if (!existing) {
      script.src = CHECKOUT_SRC;
      script.async = true;
      document.body.appendChild(script);
    }
  });

  return scriptPromise;
}

export const paymentService = {
  /** Whether this server can take payments at all, and with which key. */
  async getConfig(): Promise<PaymentConfig> {
    return apiClient.get<PaymentConfig>('/payments/config');
  },

  /** Open (or re-open) the order for one registration. The server sets the amount. */
  async createOrder(registrationId: string): Promise<PaymentOrder> {
    return apiClient.post<PaymentOrder>(`/payments/registrations/${registrationId}/order`, {});
  },

  /** Hand Checkout's result to the server, which verifies it and confirms the entry. */
  async verify(result: CheckoutSuccess): Promise<{ payment: PaymentRecord; registration: Registration }> {
    return apiClient.post('/payments/verify', result);
  },

  /** Every attempt against one entry, newest first. */
  async listForRegistration(registrationId: string): Promise<PaymentRecord[]> {
    return apiClient.get<PaymentRecord[]>(`/payments/registrations/${registrationId}`);
  },

  /**
   * The whole payment: open an order, take the money, confirm the entry.
   *
   * Resolves only once the SERVER has confirmed the entry. Checkout's own
   * success handler firing is not enough -- it means money moved, not that
   * this application knows it did -- and treating it as success is how a
   * player ends up paid and unregistered.
   *
   * Rejects with PaymentDismissedError if they closed the window.
   */
  async payForRegistration(registrationId: string): Promise<{
    payment: PaymentRecord;
    registration: Registration;
  }> {
    const [order] = await Promise.all([
      this.createOrder(registrationId),
      loadCheckoutScript(),
    ]);

    const Razorpay = (window as any).Razorpay;
    if (!Razorpay) {
      throw new Error('The payment window is unavailable. Please try again.');
    }

    return new Promise((resolve, reject) => {
      // Guards the pair of handlers below. Razorpay fires `ondismiss` when the
      // modal closes -- including the close that follows a SUCCESSFUL payment
      // -- so without this a completed payment can be reported as abandoned by
      // whichever callback happens to run second.
      let done = false;

      const checkout = new Razorpay({
        key: order.keyId,
        amount: order.amount,
        currency: order.currency,
        name: order.tournamentName,
        description: 'Tournament entry fee',
        order_id: order.orderId,
        prefill: order.prefill,
        theme: { color: '#0B5D3B' },
        handler: async (response: CheckoutSuccess) => {
          if (done) return;
          done = true;
          try {
            resolve(await paymentService.verify(response));
          } catch (e) {
            reject(e);
          }
        },
        modal: {
          ondismiss: () => {
            if (done) return;
            done = true;
            reject(new PaymentDismissedError());
          },
        },
      });

      // A payment Razorpay itself reports as failed: surface its reason rather
      // than the generic dismissal that follows when the modal then closes.
      checkout.on?.('payment.failed', (event: any) => {
        if (done) return;
        done = true;
        const description = event?.error?.description;
        reject(new Error(description || 'The payment did not go through. Please try again.'));
      });

      checkout.open();
    });
  },
};
