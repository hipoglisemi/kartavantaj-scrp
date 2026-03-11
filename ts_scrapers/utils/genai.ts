import { VertexAI } from '@google-cloud/vertexai';
import { GoogleGenerativeAI } from '@google/generative-ai';

const useVertexAi = process.env.USE_VERTEX_AI === 'True';
const projectId = process.env.GOOGLE_CLOUD_PROJECT || '';
const location = process.env.GOOGLE_CLOUD_LOCATION || '';

// For local dev, we might use vertex-key.json
// For production (GitHub), we use GOOGLE_APPLICATION_CREDENTIALS_JSON path (which we handle in Python, but here we expect the env var to be set)

let vertexAi: VertexAI | null = null;
let genAI: GoogleGenerativeAI | null = null;

if (useVertexAi) {
    vertexAi = new VertexAI({ project: projectId, location: location });
} else {
    genAI = new GoogleGenerativeAI(process.env.GOOGLE_GEMINI_KEY || process.env.GEMINI_API_KEY || '');
}

export async function generateContent(prompt: string, modelName: string = 'gemini-2.5-flash-lite', options: any = {}) {
    try {
        if (vertexAi) {
            const model = vertexAi.getGenerativeModel({
                model: modelName,
                generationConfig: options.generationConfig
            });
            const result = await model.generateContent(prompt);
            return result.response.candidates?.[0]?.content?.parts?.[0]?.text || '';
        } else if (genAI) {
            const model = genAI.getGenerativeModel({
                model: modelName,
                generationConfig: options.generationConfig
            });
            const result = await model.generateContent(prompt);
            return result.response.text();
        }
        throw new Error("AI Provider not configured");
    } catch (error) {
        console.error(`[AI Utility] Error:`, error);
        throw error;
    }
}
