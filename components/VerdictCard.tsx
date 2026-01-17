
import React from 'react';

interface VerdictCardProps {
  score: number;
}

const VerdictCard: React.FC<VerdictCardProps> = ({ score }) => {
  const getColor = (s: number) => {
    if (s > 80) return 'text-emerald-400';
    if (s > 60) return 'text-sky-400';
    if (s > 40) return 'text-yellow-400';
    return 'text-rose-500';
  };

  return (
    <div className="vola-card p-8 rounded-2xl flex flex-col items-center justify-center text-center relative overflow-hidden group">
      <div className="absolute inset-0 bg-blue-500/5 group-hover:bg-blue-500/10 transition-colors" />
      <h3 className="text-white/40 text-xs font-bold uppercase tracking-widest mb-2 relative z-10">Vola Verdict</h3>
      <div className={`text-7xl font-black mb-2 tracking-tighter relative z-10 ${getColor(score)}`}>
        {score}
      </div>
      <div className="text-white/60 text-sm relative z-10 font-medium">Health Rating</div>
      
      {/* Visual meter */}
      <div className="w-full h-1 bg-white/10 mt-6 rounded-full overflow-hidden relative z-10">
        <div 
          className={`h-full vola-gradient transition-all duration-1000`} 
          style={{ width: `${score}%` }} 
        />
      </div>
    </div>
  );
};

export default VerdictCard;
