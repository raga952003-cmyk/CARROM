import { apiClient } from '../utils/apiClient';

export interface PaymentProof {
  id: string;
  registrationId: string;
  tournamentId: string;
  submittedBy: string;
  transactionReference: string;
  payeeUpiId: string;
  status: 'pending' | 'approved' | 'rejected';
  amountPaise: number;
  submittedAt: string;
  reviewedAt?: string | null;
  reviewNote?: string | null;
  fileUrl?: string | null;
  mimeType: string;
  imageAnalysis?: {
    scanStatus: 'scanned' | 'unreadable' | 'unavailable' | 'not_configured' | 'pdf_not_scanned';
    similarImageCount: number;
    referenceRead?: string | null;
    referenceMatches?: boolean | null;
    amountPaiseRead?: number | null;
    amountMatches?: boolean | null;
  };
}

export const paymentProofService = {
  listForRegistration(registrationId: string): Promise<PaymentProof[]> {
    return apiClient.get(`/registrations/${registrationId}/payment-proofs`);
  },

  listForTournament(tournamentId: string): Promise<PaymentProof[]> {
    return apiClient.get(`/tournaments/${tournamentId}/payment-proofs`);
  },

  submit(registrationId: string, transactionReference: string, file: File): Promise<{
    status: 'pending_review'; proof: PaymentProof; message: string;
  }> {
    const data = new FormData();
    data.append('transaction_reference', transactionReference);
    data.append('file', file);
    return apiClient.post(`/registrations/${registrationId}/payment-proof`, data);
  },

  review(proofId: string, decision: 'approved' | 'rejected', note: string, confirmedReceived = false): Promise<{ proof: PaymentProof }> {
    return apiClient.post(`/payment-proofs/${proofId}/review`, {
      decision, note, confirmed_received: confirmedReceived,
    });
  },
};
