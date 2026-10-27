// Same real API, Next proxy and synthetic-data harness as the product flow.
process.env.EDU_LAYOUT_FLOW = '1';
await import('./discovery-api-flow.mjs');
