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

import { apiClient, ApiError } from '../utils/apiClient';
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

/**
 * Money left the account but this app could not get confirmation recorded.
 *
 * A distinct type because the two failures either side of the payment need
 * opposite wording. Before the payment, "try again" is correct advice. After
 * it, "try again" invites a second charge -- and a verify call is exactly where
 * a phone loses signal: the player has just switched back from their UPI app.
 *
 * `paid` is true by construction: this is only thrown once Razorpay's success
 * handler has fired, which means the charge went through.
 */
export class PaymentUnconfirmedError extends Error {
  readonly paid = true;
  readonly paymentId: string;
  constructor(paymentId: string) {
    super(
      'Your payment went through, but we could not confirm it just now. ' +
      'Do not pay again — it will be confirmed automatically, or the organisers can confirm it.'
    );
    this.name = 'PaymentUnconfirmedError';
    this.paymentId = paymentId;
  }
}

/** Verify attempts, and the pause between them. */
const VERIFY_ATTEMPTS = 3;
const VERIFY_BACKOFF_MS = [600, 1800];

const pause = (ms: number) => new Promise<void>(r => setTimeout(r, ms));

/**
 * Hand Checkout's result to the server, retrying a transport failure.
 *
 * Only transport and 5xx failures are retried. A 4xx is the server's decision
 * -- a bad signature, an amount mismatch, an entry already paid -- and repeating
 * the call cannot change it.
 *
 * When every attempt fails, the caller gets PaymentUnconfirmedError rather than
 * the raw network error, because at this point the money HAS moved and the
 * difference matters more than the cause. The webhook is the backstop: Razorpay
 * redelivers, and the server settles the entry without the browser.
 */
async function verifyWithRetry(
  response: CheckoutSuccess,
): Promise<{ payment: PaymentRecord; registration: Registration }> {
  let lastFailure: unknown = null;

  for (let attempt = 0; attempt < VERIFY_ATTEMPTS; attempt++) {
    try {
      return await paymentService.verify(response);
    } catch (e: any) {
      lastFailure = e;
      const status = e instanceof ApiError ? e.status : 0;
      // A decision, not a blip: stop and surface it.
      if (status >= 400 && status < 500 && status !== 408 && status !== 429) {
        throw e;
      }
      if (attempt < VERIFY_ATTEMPTS - 1) {
        await pause(VERIFY_BACKOFF_MS[attempt] ?? 1800);
      }
    }
  }

  console.error('Payment verification failed after retries:', lastFailure);
  throw new PaymentUnconfirmedError(response.razorpay_payment_id);
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
    // A tag already in the document is NOT reusable.
    //
    // If it had loaded, window.Razorpay would exist and we would have returned
    // above -- so reaching here with a tag present means that tag is dead: its
    // load/error events have already fired and will never fire again. Binding
    // listeners to it produced a promise that could not settle, and the payment
    // panel spun forever. A flaky connection or a blocked CDN on the first
    // attempt made every later attempt hang, not just that one.
    //
    // So the corpse is removed and a fresh tag injected.
    const dead = document.querySelectorAll<HTMLScriptElement>(`script[src="${CHECKOUT_SRC}"]`);
    dead.forEach(node => node.remove());

    const script = document.createElement('script');

    script.addEventListener('load', () => {
      // Guard against a script that loads but does not define the global --
      // a captive-portal or proxy interstitial served with a 200.
      if ((window as any).Razorpay) {
        resolve();
      } else {
        scriptPromise = null;
        script.remove();
        reject(new Error('The payment window did not load correctly. Please try again.'));
      }
    });

    script.addEventListener('error', () => {
      // Clear the cache AND remove the element, so the next attempt starts
      // clean rather than finding this one and waiting on it.
      scriptPromise = null;
      script.remove();
      reject(new Error('Could not load the payment window. Check your connection and try again.'));
    });

    script.src = CHECKOUT_SRC;
    script.async = true;
    document.body.appendChild(script);
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
      // Guards the handlers below. Razorpay fires `ondismiss` when the modal
      // closes -- including the close that follows a SUCCESSFUL payment -- so
      // without this a completed payment can be reported as abandoned by
      // whichever callback happens to run second.
      let done = false;

      // The reason the LAST attempt failed, if any.
      //
      // Checkout lets the customer retry inside the same modal: a declined card
      // does not close it, it shows Razorpay's own "try again" screen. So
      // `payment.failed` is NOT the end of the session and must not settle this
      // promise -- it used to, which meant a player whose card was declined and
      // who then paid successfully by UPI in the same modal had that success
      // silently discarded: `handler` hit the latch and returned, the server was
      // never told, and the app showed "your card was declined" over a payment
      // that had gone through. The natural next step is to pay again.
      //
      // The failure is remembered instead, and only reported if the player
      // gives up and dismisses the modal.
      let lastError: string | null = null;

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
            resolve(await verifyWithRetry(response));
          } catch (e) {
            reject(e);
          }
        },
        modal: {
          ondismiss: () => {
            if (done) return;
            done = true;
            // Dismissed after a failed attempt is a failure to report;
            // dismissed with nothing attempted is simply a change of mind.
            reject(lastError ? new Error(lastError) : new PaymentDismissedError());
          },
        },
      });

      // An attempt Razorpay reports as failed. Remembered, NOT settled: the
      // modal stays open on its retry screen, and the next attempt may well
      // succeed. See the note on `lastError` above.
      checkout.on?.('payment.failed', (event: any) => {
        if (done) return;
        lastError = event?.error?.description
          || 'The payment did not go through. Please try again.';
      });

      checkout.open();
    });
  },
};
