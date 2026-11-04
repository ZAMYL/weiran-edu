// Defaults to the complete acceptance, which must fail until the protocol is fixed.
// EDU_EXPECT_INTEGER_REJECTION=1 records only a reproduction, never a product pass.
process.env.EDU_INTEGER_FLOW = '1';
await import('./discovery-api-flow.mjs');
