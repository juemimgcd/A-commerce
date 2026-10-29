import { Type, type Static } from "typebox";

export const uuid = Type.String({ format: "uuid" });
export const requestSchema = Type.Object(
  {
    run_id: uuid,
    user_id: uuid,
    conversation_id: uuid,
    memory_generation: Type.Integer({ minimum: 1 }),
    previous_run_id: Type.Union([uuid, Type.Null()]),
    token: Type.String({ minLength: 32, maxLength: 128 }),
    message: Type.String({ minLength: 1, maxLength: 2000 }),
    history: Type.Array(
      Type.Object(
        {
          user: Type.String({ maxLength: 2000 }),
          assistant: Type.String({ maxLength: 4000 }),
        },
        { additionalProperties: false },
      ),
      { maxItems: 6 },
    ),
  },
  { additionalProperties: false },
);
export type RunRequest = Static<typeof requestSchema>;

export const replySchema = Type.Object({
  text: Type.String({ minLength: 1, maxLength: 12000 }),
  session_id: uuid,
  outcome: Type.Union([Type.Literal("answered"), Type.Literal("limited")]),
  input_tokens: Type.Number({ minimum: 0 }),
  output_tokens: Type.Number({ minimum: 0 }),
  cost: Type.Number({ minimum: 0 }),
  total_tokens: Type.Number({ minimum: 0 }),
  provider: Type.String({ maxLength: 100 }),
  model: Type.String({ maxLength: 200 }),
});
export type RunReply = Static<typeof replySchema>;
