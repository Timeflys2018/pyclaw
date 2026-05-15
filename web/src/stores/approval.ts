import { create } from 'zustand'
import type { PendingApproval } from '../types'

interface ApprovalState {
  pendingApproval: PendingApproval | null
}

interface ApprovalActions {
  setPendingApproval: (approval: PendingApproval | null) => void
  clearPendingApproval: () => void
}

type ApprovalStore = ApprovalState & ApprovalActions

export const useApprovalStore = create<ApprovalStore>((set) => ({
  pendingApproval: null,
  setPendingApproval: (pendingApproval) => set({ pendingApproval }),
  clearPendingApproval: () => set({ pendingApproval: null }),
}))
