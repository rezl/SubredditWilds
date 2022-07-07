#!/usr/bin/env node

'use strict';

const snoowrap = require('snoowrap');
const config = require('./config.json');
const jeeves = require('./jeeves');

if (require.main === module) {
	let m = new jeeves(config, snoowrap);
	console.log(`Begin polling every ${config.polling_interval / 1000} seconds...`);
	m.mainloop();
} else {
	module.exports = jeeves;
}