'use strict';

module.exports = function(config, snoowrap) {
	const r = new snoowrap(config);

	var last_modact_time = 0;

	var me = this;

	this.process = function(modacts) {
		modacts.reverse().forEach(function(modact) {
			if (modact.created_utc > last_modact_time) {
				if (modact.details !== 'confirm_spam' && modact.mod !== 'AutoModerator') {
					// get removed submission
					r.getSubmission(me.get_id(modact.target_fullname)).fetch().then(function(submission) {
						// post new submission in destination sub
						r.getSubreddit(config.destination_sub).submitLink({
							title: `[${submission.score}] ${submission.title}`,
							url: 'https://np.reddit.com' + submission.permalink,
							sendReplies: false
						}).then(function() {
							console.log(`Successfully submitted removed post '${modact.target_title}'`);
						}).catch(function(error) {
							console.log(`[ERROR] Error submitting removed post: ${error.name}: ${error.message}`);
						});
					}).catch(function(error) {
						console.log(`[ERROR] Error getting removed post: ${error.name}: ${error.message}`);
					});

					last_modact_time = modact.created_utc;
				}
			}
		});
	};

	this.get_id = function(fullname) {
		const id_part = /_(\w+)/;

		let match = fullname.match(id_part);

		return match !== null ? match[1] : match;
	};

	this.mainloop = function() {
		if (last_modact_time === 0) {
			console.log(`Mainloop initializing...`);

			// initialize by getting last moderation action and storing its timestamp
			r.getSubreddit(config.target_sub).getModerationLog({ limit: 1}).then(function(modacts) {
				last_modact_time = modacts[0].created_utc;
				console.log(`Initialized with last moderator action, timestamp: ${last_modact_time}`);
			})
			.catch(function(error) {
				console.log(`[ERROR] Error initializing mainloop: ${error.name}: ${error.message}`);
			})
			.finally(function() {
				console.log(`Mainloop initialized, starting loop...`);
				setTimeout(me.mainloop, config.polling_interval);
			});
		} else {
			r.getSubreddit(config.target_sub).getModerationLog({ limit: 15, type: 'removelink' }).then(function(modacts) {
				me.process(modacts);
			})
			.catch(function(error) {
				console.log(`[ERROR] Error getting moderator actions: ${error.name}: ${error.message}`);
			})
			.finally(function() {
				setTimeout(me.mainloop, config.polling_interval);
			});
		}
	};
};