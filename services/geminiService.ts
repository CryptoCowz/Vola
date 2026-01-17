import { GoogleGenAI, Type } from "@google/genai";
import { AuditResponse, Transaction } from "../types";

const SYSTEM_INSTRUCTION = `
Role: You are Vola, a strategic financial AI designed to automate "adulting" and accelerate financial independence.
Task: Analyze provided transaction logs.

Instructions:
1. Calculate the "Burn Rate": What percentage of income was spent immediately? (Total Spend / Total Income).
2. Identify "Leakage": Spot emotional or frictionless spending (e.g., dining out, excessive coffee, impulse tech buys, unused subscriptions).
   - Provide a specific reason WHY it's leakage (e.g., "Frequent high-margin retail spend vs home utility").
   - Suggest a concrete, "Actionable Alternative" to reduce or replace this spend (e.g., "Cancel unused subscriptions like Netflix for immediate $18.99/mo savings" or "Switch to bulk-buy coffee to reduce cost by 85%").
3. The "Vola Verdict": Give a harsh but constructive score (0-100) on their financial health. 100 is perfect discipline.
4. Crypto/Asset Check: Acknowledge asset accumulation (like Coinbase, stocks, or gold) as "deploying capital" rather than "spending." Do not count these as Burn Rate expenses if possible, treat them as transfers to wealth.
5. Tone: Direct, data-driven, slightly futuristic, cold but helpful. No fluff.

Return the result strictly as a JSON object matching the provided schema.
`;

export async function auditFinancials(csvData: string): Promise<AuditResponse> {
  const ai = new GoogleGenAI({ apiKey: process.env.API_KEY || "" });
  
  const response = await ai.models.generateContent({
    model: "gemini-3-flash-preview",
    contents: `Analyze this CSV data:\n\n${csvData}`,
    config: {
      systemInstruction: SYSTEM_INSTRUCTION,
      responseMimeType: "application/json",
      responseSchema: {
        type: Type.OBJECT,
        properties: {
          burnRatePercentage: { type: Type.NUMBER, description: "Percentage of income spent (0-100)" },
          leakageItems: {
            type: Type.ARRAY,
            items: {
              type: Type.OBJECT,
              properties: {
                item: { type: Type.STRING },
                reason: { type: Type.STRING },
                alternative: { type: Type.STRING, description: "Actionable step to reduce this leakage" }
              },
              required: ["item", "reason", "alternative"]
            }
          },
          volaVerdictScore: { type: Type.NUMBER, description: "Health score (0-100)" },
          assetAccumulationSummary: { type: Type.STRING },
          detailedReasoning: { type: Type.STRING, description: "Markdown summary of the audit" },
          categorySpending: {
            type: Type.ARRAY,
            items: {
              type: Type.OBJECT,
              properties: {
                category: { type: Type.STRING },
                total: { type: Type.NUMBER }
              },
              required: ["category", "total"]
            }
          }
        },
        required: ["burnRatePercentage", "leakageItems", "volaVerdictScore", "assetAccumulationSummary", "detailedReasoning", "categorySpending"]
      }
    }
  });

  if (!response.text) {
    throw new Error("No response from Vola.");
  }

  return JSON.parse(response.text.trim());
}

export function parseCSV(csv: string): Transaction[] {
  const lines = csv.trim().split("\n");
  const headers = lines[0].toLowerCase().split(",");
  
  return lines.slice(1).map(line => {
    const values = line.split(",");
    const entry: any = {};
    headers.forEach((header, i) => {
      let val: any = values[i];
      if (header === 'amount') {
        val = parseFloat(val.replace(/[+]/g, ''));
      }
      entry[header] = val;
    });
    return entry as Transaction;
  });
}
