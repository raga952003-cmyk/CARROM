from pydantic import BaseModel


class PaymentVerifySchema(BaseModel):
    """
    What Razorpay Checkout hands back to the browser on success.

    Field names are Razorpay's own, in snake_case, and are NOT camelised the
    way the rest of this API's payloads are: they arrive verbatim from
    Checkout's success handler, and renaming them on the way through would mean
    the frontend rewriting a provider payload before sending it -- an easy
    place to drop the signature and a pointless one to invent a mapping.

    Every one of these values comes from the client and none may be trusted
    before `verify_payment_signature` has checked the HMAC over them.
    """
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
