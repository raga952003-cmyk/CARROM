import { GoogleGenAI } from "@google/genai";

let aiClient: GoogleGenAI | null = null;

export function getGeminiClient(): GoogleGenAI | null {
  if (aiClient) return aiClient;

  // Search Vite env metadata, then process.env from define plugin
  const apiKey = (import.meta as any).env.VITE_GEMINI_API_KEY || 
                 (import.meta as any).env.GEMINI_API_KEY || 
                 (typeof process !== 'undefined' && process.env?.GEMINI_API_KEY);

  if (apiKey && apiKey !== "MY_GEMINI_API_KEY") {
    try {
      aiClient = new GoogleGenAI({ apiKey });
    } catch (e) {
      console.error("Failed to initialize Gemini AI client:", e);
    }
  }
  return aiClient;
}
