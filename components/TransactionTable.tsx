
import React from 'react';
import { Transaction } from '../types';

interface TransactionTableProps {
  transactions: Transaction[];
}

const TransactionTable: React.FC<TransactionTableProps> = ({ transactions }) => {
  return (
    <div className="overflow-x-auto rounded-xl vola-card">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-white/10 text-white/40 uppercase tracking-widest text-xs font-bold">
            <th className="px-6 py-4 font-medium">Date</th>
            <th className="px-6 py-4 font-medium">Description</th>
            <th className="px-6 py-4 font-medium">Category</th>
            <th className="px-6 py-4 font-medium text-right">Amount</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/5">
          {transactions.map((t, i) => (
            <tr key={i} className="hover:bg-white/5 transition-colors">
              <td className="px-6 py-4 whitespace-nowrap text-white/60 mono">{t.date}</td>
              <td className="px-6 py-4 font-medium text-white/90">{t.description}</td>
              <td className="px-6 py-4">
                <span className="px-2 py-1 rounded-full bg-white/10 text-white/70 text-xs">
                  {t.category}
                </span>
              </td>
              <td className={`px-6 py-4 text-right mono font-bold ${t.amount > 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                {t.amount > 0 ? '+' : ''}{t.amount.toFixed(2)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

export default TransactionTable;
