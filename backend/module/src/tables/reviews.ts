import { table, t } from 'spacetimedb/server';

export const recommendationReviews = table(
  { name: 'recommendation_reviews', public: true },
  {
    reportId: t.string().primaryKey(),
    requestId: t.string().unique(),
    reviewerIdentity: t.identity(),
    reviewedAtUtc: t.timestamp(),
    contentJson: t.string(),
  },
);
