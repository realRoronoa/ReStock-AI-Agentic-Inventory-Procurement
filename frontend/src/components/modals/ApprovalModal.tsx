import { useState } from 'react'
import { formatInr } from '@/utils/format'
import { useApproveOrder } from '@/hooks/useOrders'
import { useToast } from '@/context/ToastContext'

interface ApprovalModalProps {
  orderId: number
  productName: string
  quantity: number
  unit: string
  supplierName: string
  amountPaise: number
  onClose: () => void
  onApproved: (orderId: number) => void
}

export function ApprovalModal({
  orderId,
  productName,
  quantity,
  unit,
  supplierName,
  amountPaise,
  onClose,
  onApproved,
}: ApprovalModalProps) {
  const [pipelineStep, setPipelineStep] = useState<number | null>(null)
  const approveOrder = useApproveOrder()
  const { showToast } = useToast()

  const handleStartApproval = () => {
    setPipelineStep(0)

    // Execute the backend approval endpoint
    approveOrder.mutate(orderId, {
      onSuccess: () => {
        setPipelineStep(1)
        setTimeout(() => {
          setPipelineStep(2)
          setTimeout(() => {
            showToast('Payout initiated')
            onApproved(orderId)
          }, 800)
        }, 700)
      },
      onError: (err) => {
        showToast(`Approval failed: ${err.message}`)
        onClose()
      },
    })
  }

  return (
    <div
      className="overlay"
      id="ovConfirm"
      onClick={(e) => {
        if (e.target === e.currentTarget && pipelineStep === null) onClose()
      }}
    >
      <div className="modal fade-in-fast">
        {pipelineStep === null ? (
          <>
            <div className="modal-head">
              <h3>Confirm Purchase</h3>
            </div>
            <div className="modal-body">
              <div
                style={{
                  fontSize: '13px',
                  color: 'var(--text-soft)',
                  marginBottom: '12px',
                }}
              >
                You are approving this purchase:
              </div>
              <div className="confirm-line">
                <span className="k">Product</span>
                <span className="v">{productName}</span>
              </div>
              <div className="confirm-line">
                <span className="k">Quantity</span>
                <span className="v mono">
                  {quantity} {unit}
                </span>
              </div>
              <div className="confirm-line">
                <span className="k">Supplier</span>
                <span className="v">{supplierName}</span>
              </div>
              <div className="confirm-line">
                <span className="k">Total</span>
                <span className="v mono">{formatInr(amountPaise)}</span>
              </div>
              <div className="confirm-note">
                The AI recommended this purchase, but <strong>you</strong> are authorizing
                the payment. This action will initiate a RazorpayX supplier payout.
              </div>
            </div>
            <div className="modal-foot">
              <button
                type="button"
                className="btn btn-secondary"
                id="btnCancelConfirm"
                onClick={onClose}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-approve"
                id="btnConfirmApprove"
                onClick={handleStartApproval}
              >
                Confirm Approval
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="modal-head">
              <h3>Processing Approval</h3>
            </div>
            <div className="modal-body">
              <div className="progress-steps" id="pSteps">
                <div
                  className={`progress-step ${
                    pipelineStep === 0 ? 'active' : pipelineStep > 0 ? 'done' : ''
                  }`}
                >
                  <span className="mark">{pipelineStep > 0 ? '✓' : ''}</span>
                  <span>Approval received</span>
                </div>
                <div
                  className={`progress-step ${
                    pipelineStep === 1 ? 'active' : pipelineStep > 1 ? 'done' : ''
                  }`}
                >
                  <span className="mark">{pipelineStep > 1 ? '✓' : ''}</span>
                  <span>Creating supplier payout…</span>
                </div>
                <div
                  className={`progress-step ${pipelineStep === 2 ? 'active' : ''}`}
                >
                  <span className="mark" />
                  <span>Payout initiated — waiting for Razorpay confirmation…</span>
                </div>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
