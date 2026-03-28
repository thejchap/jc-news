from tryke import expect, test


@test("basic")
def test_basic():
    expect(1 + 1, "1 + 1").to_equal(2)
