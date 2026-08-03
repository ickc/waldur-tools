/**
 * The whole background script: open the report in a tab.
 *
 * A popup would be the conventional home for a toolbar action, but this report
 * is a page -- six figures, a table under each and a method section -- and a
 * popup that closes when the reader clicks away would throw the pull away with
 * it. A tab also gives the page an extension origin, which is what lets it read
 * the API at all: the deployment's CORS allowlist holds only the portal's own
 * front end, and `host_permissions` is the way around that.
 */

chrome.action.onClicked.addListener(() => {
  chrome.tabs.create({ url: chrome.runtime.getURL('src/report.html') });
});
