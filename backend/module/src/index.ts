import { schema } from 'spacetimedb/server';

// Reserved module entry point. Tables, authorization and reducers are pending.
// Do not publish this empty module as the implemented advisory backend.
const spacetimedb = schema({});
export default spacetimedb;
