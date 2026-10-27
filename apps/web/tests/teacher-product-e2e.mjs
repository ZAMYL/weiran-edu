// No HTTP mocks: uses the same real-service harness as PR 55.
process.env.EDU_PRODUCT_FLOW = '1';
await import('./discovery-api-flow.mjs');
