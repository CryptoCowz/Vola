export interface Transaction {
  date: string;
  description: string;
  category: string;
  amount: number;
}

export interface AuditResponse {
  burnRatePercentage: number;
  leakageItems: {
    item: string;
    reason: string;
    alternative: string;
  }[];
  volaVerdictScore: number;
  assetAccumulationSummary: string;
  detailedReasoning: string;
  categorySpending: {
    category: string;
    total: number;
  }[];
}

export interface SavedAudit {
  id: string;
  timestamp: string;
  data: AuditResponse;
  rawCsv: string;
}
