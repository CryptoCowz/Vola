import React, { useState, useCallback, useEffect, useMemo, useRef } from 'react';
import { auditFinancials, parseCSV } from './services/geminiService';
import { AuditResponse, Transaction, SavedAudit } from './types';
import TransactionTable from './components/TransactionTable';
import VerdictCard from './components/VerdictCard';
import { PieChart, Pie, Cell, ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid } from 'recharts';

const DUMMY_CSV = `Date,Description,Category,Amount
2026-01-12,Starbucks,Coffee,-7.50
2026-01-12,Coinbase,Investment,-200.00
2026-01-13,Uber Eats,Dining,-45.20
2026-01-14,Shell Station,Gas,-50.00
2026-01-15,Netflix,Subscription,-18.99
2026-01-15,Direct Deposit,Income,+2500.00
2026-01-16,Apple Store,Tech,-1200.00
2026-01-16,Steam,Entertainment,-60.00`;

const COLORS = ['#4facfe', '#00f2fe', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'];
const STORAGE_KEY = 'vola_audit_history';

const App: React.FC = () => {
  const [csvText, setCsvText] = useState(DUMMY_CSV);
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [audit, setAudit] = useState<AuditResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<SavedAudit[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Load history from localStorage
  useEffect(() => {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      try {
        setHistory(JSON.parse(saved));
      } catch (e) {
        console.error("Failed to parse history", e);
      }
    }
  }, []);

  // Save history to localStorage
  const saveToHistory = useCallback((data: AuditResponse, rawCsv: string) => {
    const newAudit: SavedAudit = {
      id: crypto.randomUUID(),
      timestamp: new Date().toISOString(),
      data,
      rawCsv
    };
    const updatedHistory = [newAudit, ...history].slice(0, 50); // Keep last 50
    setHistory(updatedHistory);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(updatedHistory));
  }, [history]);

  const handleAudit = useCallback(async () => {
    if (!csvText.trim()) {
      setError("Data feed is empty. Please provide CSV transactions.");
      return;
    }
    try {
      setLoading(true);
      setError(null);
      const data = parseCSV(csvText);
      setTransactions(data);
      const result = await auditFinancials(csvText);
      setAudit(result);
      saveToHistory(result, csvText);
    } catch (err: any) {
      console.error(err);
      setError(err.message || "Failed to audit data.");
    } finally {
      setLoading(false);
    }
  }, [csvText, saveToHistory]);

  const handleFileUpload = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (e) => {
      const content = e.target?.result as string;
      if (content) {
        setCsvText(content);
        setError(null);
      }
    };
    reader.onerror = () => {
      setError("Failed to read the file.");
    };
    reader.readAsText(file);
    // Reset file input so same file can be uploaded again if needed
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const triggerFileUpload = () => {
    fileInputRef.current?.click();
  };

  const loadHistoricalAudit = (saved: SavedAudit) => {
    setAudit(saved.data);
    setCsvText(saved.rawCsv);
    setTransactions(parseCSV(saved.rawCsv));
    setShowHistory(false);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const clearHistory = () => {
    if (confirm("Clear all historical audit data?")) {
      setHistory([]);
      localStorage.removeItem(STORAGE_KEY);
    }
  };

  const trendData = useMemo(() => {
    return [...history].reverse().map(h => ({
      date: new Date(h.timestamp).toLocaleDateString(),
      score: h.data.volaVerdictScore,
      burn: h.data.burnRatePercentage
    }));
  }, [history]);

  return (
    <div className="min-h-screen pb-20 px-4 md:px-8 bg-[#050505]">
      {/* Navigation Header */}
      <header className="max-w-7xl mx-auto pt-8 pb-12 flex flex-col md:flex-row items-center justify-between gap-6 border-b border-white/5 mb-12">
        <div>
          <h1 className="text-4xl font-black tracking-tighter text-white flex items-center gap-2">
            <span className="bg-white text-black px-2 py-1 rounded">VOLA</span>
            <span className="text-blue-400">AUDITOR</span>
          </h1>
          <p className="text-white/40 mt-1 font-medium text-sm tracking-wide uppercase">Strategic Financial Intelligence Protocol v3.1</p>
        </div>
        <div className="flex items-center gap-6">
          <button 
            onClick={() => setShowHistory(!showHistory)}
            className="text-xs font-bold text-white/60 hover:text-white uppercase tracking-widest flex items-center gap-2 transition-colors"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            History ({history.length})
          </button>
          <div className="text-right hidden sm:block border-l border-white/10 pl-6">
            <div className="text-xs font-bold text-white/30 uppercase tracking-widest">System Status</div>
            <div className="text-emerald-400 text-sm font-bold flex items-center justify-end gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              ONLINE
            </div>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto">
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-8">
          
          {/* History Sidebar / Panel (Conditional) */}
          {showHistory && (
            <div className="lg:col-span-4 mb-8 animate-in slide-in-from-top-4 fade-in duration-300">
              <div className="vola-card p-6 rounded-2xl">
                <div className="flex justify-between items-center mb-6">
                  <h3 className="text-white/40 text-xs font-bold uppercase tracking-widest">Audit History</h3>
                  <button onClick={clearHistory} className="text-rose-400/60 hover:text-rose-400 text-[10px] font-bold uppercase tracking-widest">Clear Records</button>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
                  {history.map((h) => (
                    <button 
                      key={h.id}
                      onClick={() => loadHistoricalAudit(h)}
                      className="text-left vola-card p-4 rounded-xl border-white/5 hover:border-blue-500/50 transition-all hover:bg-white/5"
                    >
                      <div className="text-white/40 text-[10px] mono mb-1">{new Date(h.timestamp).toLocaleString()}</div>
                      <div className="flex justify-between items-center">
                        <span className="text-white font-bold">Verdict: {h.data.volaVerdictScore}</span>
                        <span className={`text-[10px] font-bold ${h.data.burnRatePercentage > 40 ? 'text-rose-400' : 'text-emerald-400'}`}>
                          {h.data.burnRatePercentage.toFixed(0)}% Burn
                        </span>
                      </div>
                    </button>
                  ))}
                  {history.length === 0 && (
                    <div className="col-span-full py-12 text-center text-white/20 italic text-sm">No historical data found.</div>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* Left Column: Controls & Input */}
          <div className="lg:col-span-1 space-y-6">
            <div className="vola-card p-6 rounded-2xl">
              <div className="flex justify-between items-center mb-3">
                <label className="block text-xs font-bold text-white/40 uppercase tracking-widest">
                  Raw CSV Feed
                </label>
                <button 
                  onClick={triggerFileUpload}
                  className="text-blue-400 hover:text-blue-300 transition-colors flex items-center gap-1.5"
                >
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
                  </svg>
                  <span className="text-[10px] font-bold uppercase tracking-widest">Upload File</span>
                </button>
                <input 
                  type="file" 
                  ref={fileInputRef}
                  className="hidden" 
                  accept=".csv" 
                  onChange={handleFileUpload}
                />
              </div>
              
              <textarea
                value={csvText}
                onChange={(e) => setCsvText(e.target.value)}
                className="w-full h-80 bg-black/40 border border-white/10 rounded-xl p-4 mono text-xs text-white/80 focus:outline-none focus:border-blue-500/50 transition-all resize-none mb-4"
                placeholder="Paste CSV data here..."
              />
              
              <button
                onClick={handleAudit}
                disabled={loading}
                className={`w-full py-4 rounded-xl font-bold tracking-widest uppercase text-sm transition-all vola-gradient text-black hover:scale-[1.02] active:scale-100 disabled:opacity-50 disabled:hover:scale-100 shadow-xl shadow-blue-500/20`}
              >
                {loading ? 'Processing Protocol...' : 'Run Audit Sequence'}
              </button>
            </div>
            
            {error && (
              <div className="p-4 bg-rose-500/10 border border-rose-500/20 text-rose-400 rounded-xl text-sm font-medium animate-in fade-in duration-300">
                {error}
              </div>
            )}

            {/* Micro Trends Chart */}
            {history.length > 1 && (
              <div className="vola-card p-6 rounded-2xl">
                <h3 className="text-white/40 text-xs font-bold uppercase tracking-widest mb-4">Health Trends</h3>
                <div className="h-32">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={trendData}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#ffffff05" vertical={false} />
                      <Line type="monotone" dataKey="score" stroke="#4facfe" strokeWidth={2} dot={false} />
                      <Line type="monotone" dataKey="burn" stroke="#ef4444" strokeWidth={1} strokeDasharray="5 5" dot={false} />
                      <Tooltip 
                        contentStyle={{ background: '#111', border: 'none', borderRadius: '8px', fontSize: '10px' }} 
                        itemStyle={{ padding: '0 2px' }}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
                <div className="flex justify-between mt-2 text-[8px] font-bold text-white/20 uppercase tracking-widest">
                  <span>Previous</span>
                  <span>Latest</span>
                </div>
              </div>
            )}
          </div>

          {/* Right Column: Results */}
          <div className="lg:col-span-3">
            {audit ? (
              <div className="space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-700">
                {/* Metrics Bar */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                  <VerdictCard score={audit.volaVerdictScore} />
                  
                  <div className="vola-card p-8 rounded-2xl flex flex-col justify-center">
                    <h3 className="text-white/40 text-xs font-bold uppercase tracking-widest mb-6">Burn Rate Intensity</h3>
                    <div className="flex items-end gap-4">
                      <div className="text-6xl font-black text-white tracking-tighter">
                        {audit.burnRatePercentage.toFixed(1)}%
                      </div>
                      <div className={`mb-2 font-bold ${audit.burnRatePercentage > 40 ? 'text-rose-400' : 'text-emerald-400'}`}>
                        {audit.burnRatePercentage > 40 ? 'CRITICAL' : 'EFFICIENT'}
                      </div>
                    </div>
                    <div className="mt-4 text-white/50 text-sm leading-relaxed">
                      Percentage of liquid capital depleted against immediate income baseline.
                    </div>
                  </div>
                </div>

                {/* Analysis Breakdown */}
                <div className="grid grid-cols-1 xl:grid-cols-2 gap-8">
                  {/* Detailed Logic */}
                  <div className="vola-card p-8 rounded-2xl">
                    <h3 className="text-white/40 text-xs font-bold uppercase tracking-widest mb-6 flex items-center gap-2">
                      <span className="w-1 h-3 bg-blue-400" /> Executive Analysis
                    </h3>
                    <div className="prose prose-invert max-w-none prose-sm">
                      <p className="text-white/80 leading-relaxed whitespace-pre-wrap italic">
                        "{audit.detailedReasoning}"
                      </p>
                    </div>
                    <div className="mt-8 pt-8 border-t border-white/5">
                      <h4 className="text-xs font-bold text-white/40 uppercase tracking-widest mb-4">Capital Deployment (Assets)</h4>
                      <p className="text-emerald-400 font-medium text-sm">
                        {audit.assetAccumulationSummary}
                      </p>
                    </div>
                  </div>

                  {/* Leakage List */}
                  <div className="vola-card p-8 rounded-2xl flex flex-col">
                    <h3 className="text-white/40 text-xs font-bold uppercase tracking-widest mb-6 flex items-center gap-2">
                      <span className="w-1 h-3 bg-rose-400" /> Efficiency Leakage
                    </h3>
                    <div className="space-y-4 flex-grow">
                      {audit.leakageItems.map((leak, idx) => (
                        <div key={idx} className="bg-white/5 border-l-2 border-rose-500 p-4 rounded-r-xl group">
                          <div className="text-white font-bold text-sm mb-1">{leak.item}</div>
                          <div className="text-white/50 text-xs mb-3">{leak.reason}</div>
                          <div className="mt-2 pt-2 border-t border-white/5">
                            <div className="text-[9px] font-bold text-emerald-400 uppercase tracking-widest mb-1 opacity-80">
                              Actionable Alternative
                            </div>
                            <div className="text-white/70 text-xs leading-relaxed font-medium">
                              {leak.alternative}
                            </div>
                          </div>
                        </div>
                      ))}
                      {audit.leakageItems.length === 0 && (
                        <div className="text-white/30 text-sm italic py-8 text-center border border-dashed border-white/10 rounded-xl">
                          No leakage detected. Maximum efficiency achieved.
                        </div>
                      )}
                    </div>

                    {/* Chart: Category Spending */}
                    <div className="h-64 mt-8">
                       <ResponsiveContainer width="100%" height="100%">
                        <PieChart>
                          <Pie
                            data={audit.categorySpending}
                            cx="50%"
                            cy="50%"
                            innerRadius={60}
                            outerRadius={80}
                            paddingAngle={5}
                            dataKey="total"
                          >
                            {audit.categorySpending.map((entry, index) => (
                              <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                            ))}
                          </Pie>
                          <Tooltip 
                            contentStyle={{ backgroundColor: '#111', border: 'none', borderRadius: '8px', color: '#fff' }}
                            itemStyle={{ color: '#fff' }}
                          />
                        </PieChart>
                      </ResponsiveContainer>
                      <div className="flex flex-wrap justify-center gap-3 mt-2">
                        {audit.categorySpending.map((entry, index) => (
                          <div key={index} className="flex items-center gap-1.5">
                            <div className="w-2 h-2 rounded-full" style={{ backgroundColor: COLORS[index % COLORS.length] }} />
                            <span className="text-[10px] font-bold text-white/40 uppercase">{entry.category}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>

                <div className="mt-12">
                   <h3 className="text-white/40 text-xs font-bold uppercase tracking-widest mb-6">Source Log Verification</h3>
                   <TransactionTable transactions={transactions} />
                </div>
              </div>
            ) : (
              <div className="vola-card h-full min-h-[500px] flex flex-col items-center justify-center text-center p-12 border-dashed">
                <div className="w-16 h-16 rounded-full bg-white/5 flex items-center justify-center mb-6">
                  <svg className="w-8 h-8 text-white/20" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01" />
                  </svg>
                </div>
                <h2 className="text-xl font-bold text-white/60 mb-2 italic">Awaiting Telemetry Data</h2>
                <p className="text-white/30 text-sm max-w-sm">
                  Upload a CSV file or paste your transaction logs into the protocol feed to begin the strategic audit sequence.
                </p>
                <div className="mt-8 flex flex-col sm:flex-row gap-4">
                  <button 
                    onClick={triggerFileUpload}
                    className="px-6 py-3 bg-white/5 hover:bg-white/10 border border-white/10 rounded-xl text-white font-bold uppercase tracking-widest text-xs transition-all flex items-center justify-center gap-2"
                  >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
                    </svg>
                    Import CSV File
                  </button>
                  {history.length > 0 && (
                    <button 
                      onClick={() => setShowHistory(true)}
                      className="px-6 py-3 text-blue-400 font-bold uppercase tracking-widest text-xs hover:text-blue-300 transition-colors flex items-center justify-center gap-2"
                    >
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                      </svg>
                      Load History
                    </button>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      </main>

      <footer className="max-w-7xl mx-auto mt-20 pt-8 border-t border-white/5 flex flex-col md:flex-row items-center justify-between gap-4 text-white/20 text-[10px] font-bold tracking-[0.2em] uppercase">
        <div>&copy; 2026 VOLA STRATEGIC INTELLIGENCE</div>
        <div className="flex gap-8">
          <span>Encrypted Session</span>
          <span>Adulting: Automated</span>
          <span>Financial Freedom: Imminent</span>
        </div>
      </footer>
    </div>
  );
};

export default App;
