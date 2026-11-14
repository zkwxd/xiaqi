// 九路围棋。权重在 weights.txt，推理是折好 BN 的残差网络，搜索是 32 次 PUCT。
#include <algorithm>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

using namespace std;

static const int C = 48;
static const int BLOCKS = 6;
static const int PASS = 81;

struct Weights {
    vector<float> stem_w, stem_b;
    vector<float> a_w[BLOCKS], a_b[BLOCKS], c_w[BLOCKS], c_b[BLOCKS];
    vector<float> pol_w, pol_b, pol_fc_w, pol_fc_b;
    vector<float> val_w, val_b, v1_w, v1_b, v2_w, v2_b;
    uint64_t zblack[81]{};
    uint64_t zwhite[81]{};
};

static Weights W;

static vector<float> read_floats(istream& in, int n) {
    vector<float> v(n);
    for (int i = 0; i < n; ++i) in >> v[i];
    return v;
}

static bool load_weights() {
    ifstream in("weights.txt");
    if (!in) return false;
    string magic;
    int channels = 0, blocks = 0;
    in >> magic >> channels >> blocks;
    if (!in || magic != "go9net" || channels != C || blocks != BLOCKS) return false;
    string name;
    int n = 0;
    auto take = [&](vector<float>& dest) {
        dest = read_floats(in, n);
        return (int)dest.size() == n;
    };
    while (in >> name >> n) {
        if (name == "stem_w") { if (!take(W.stem_w)) return false; }
        else if (name == "stem_b") { if (!take(W.stem_b)) return false; }
        else if (name == "pol_w") { if (!take(W.pol_w)) return false; }
        else if (name == "pol_b") { if (!take(W.pol_b)) return false; }
        else if (name == "pol_fc_w") { if (!take(W.pol_fc_w)) return false; }
        else if (name == "pol_fc_b") { if (!take(W.pol_fc_b)) return false; }
        else if (name == "val_w") { if (!take(W.val_w)) return false; }
        else if (name == "val_b") { if (!take(W.val_b)) return false; }
        else if (name == "v1_w") { if (!take(W.v1_w)) return false; }
        else if (name == "v1_b") { if (!take(W.v1_b)) return false; }
        else if (name == "v2_w") { if (!take(W.v2_w)) return false; }
        else if (name == "v2_b") { if (!take(W.v2_b)) return false; }
        else if (name == "zblack") {
            for (int i = 0; i < 81; ++i) in >> W.zblack[i];
        } else if (name == "zwhite") {
            for (int i = 0; i < 81; ++i) in >> W.zwhite[i];
        } else if (name.size() >= 4 && name[0] == 'b') {
            int id = name[1] - '0';
            if (id < 0 || id >= BLOCKS) return false;
            if (name.find("a_w") != string::npos) { if (!take(W.a_w[id])) return false; }
            else if (name.find("a_b") != string::npos) { if (!take(W.a_b[id])) return false; }
            else if (name.find("c_w") != string::npos) { if (!take(W.c_w[id])) return false; }
            else if (name.find("c_b") != string::npos) { if (!take(W.c_b[id])) return false; }
            else return false;
        } else return false;
    }
    return W.stem_w.size() == 12 * C * 9 && W.pol_fc_w.size() == 82 * 162;
}

static void conv3(const float* in, int ic, const float* w, const float* bias, int oc, float* out) {
    for (int o = 0; o < oc; ++o) {
        for (int y = 0; y < 9; ++y) {
            for (int x = 0; x < 9; ++x) {
                float sum = bias[o];
                for (int c = 0; c < ic; ++c) {
                    for (int ky = 0; ky < 3; ++ky) {
                        int iy = y + ky - 1;
                        if (iy < 0 || iy >= 9) continue;
                        for (int kx = 0; kx < 3; ++kx) {
                            int ix = x + kx - 1;
                            if (ix < 0 || ix >= 9) continue;
                            float wv = w[(((o * ic + c) * 3 + ky) * 3) + kx];
                            sum += wv * in[(c * 9 + iy) * 9 + ix];
                        }
                    }
                }
                out[(o * 9 + y) * 9 + x] = sum;
            }
        }
    }
}

static void conv1(const float* in, int ic, const float* w, const float* bias, int oc, float* out) {
    for (int o = 0; o < oc; ++o) {
        for (int y = 0; y < 9; ++y) {
            for (int x = 0; x < 9; ++x) {
                float sum = bias[o];
                for (int c = 0; c < ic; ++c) sum += w[o * ic + c] * in[(c * 9 + y) * 9 + x];
                out[(o * 9 + y) * 9 + x] = sum;
            }
        }
    }
}

static void relu(float* v, int n) {
    for (int i = 0; i < n; ++i) if (v[i] < 0) v[i] = 0;
}

static void forward(const float* feat, float* logits, float& value) {
    static float h1[C * 81], h2[C * 81], h3[C * 81], pol[2 * 81], val[81], hid[64];
    conv3(feat, 12, W.stem_w.data(), W.stem_b.data(), C, h1);
    if (getenv("GO9_DEBUG")) cerr << "stem0 " << h1[0] << " bias0 " << W.stem_b[0] << "\n";
    relu(h1, C * 81);
    for (int b = 0; b < BLOCKS; ++b) {
        conv3(h1, C, W.a_w[b].data(), W.a_b[b].data(), C, h2);
        relu(h2, C * 81);
        conv3(h2, C, W.c_w[b].data(), W.c_b[b].data(), C, h3);
        for (int i = 0; i < C * 81; ++i) h1[i] = h3[i] + h1[i];
        relu(h1, C * 81);
        if (getenv("GO9_DEBUG")) cerr << "block " << b << " " << h1[0] << "\n";
    }
    conv1(h1, C, W.pol_w.data(), W.pol_b.data(), 2, pol);
    relu(pol, 2 * 81);
    for (int o = 0; o < 82; ++o) {
        float sum = W.pol_fc_b[o];
        const float* row = W.pol_fc_w.data() + o * 162;
        for (int i = 0; i < 162; ++i) sum += row[i] * pol[i];
        logits[o] = sum;
    }
    conv1(h1, C, W.val_w.data(), W.val_b.data(), 1, val);
    relu(val, 81);
    for (int o = 0; o < 64; ++o) {
        float sum = W.v1_b[o];
        const float* row = W.v1_w.data() + o * 81;
        for (int i = 0; i < 81; ++i) sum += row[i] * val[i];
        hid[o] = sum > 0 ? sum : 0;
    }
    float raw = W.v2_b[0];
    for (int i = 0; i < 64; ++i) raw += W.v2_w[i] * hid[i];
    value = tanh(raw);
}

struct State {
    uint8_t board[81]{};
    int to_play = 1;
    int passes = 0;
    int passes_to_score = 2;
    int move_count = 0;
    int last = -1;
    uint64_t hash = 0;
    vector<uint64_t> seen;
    bool finished = false;
    int winner = 0;
    int taken[3]{};
};

static int NEIGH[81][4];
static int NCOUNT[81];

static void init_neighbors() {
    for (int i = 0; i < 81; ++i) {
        int x = i % 9, y = i / 9, n = 0;
        const int d[4][2] = {{1, 0}, {-1, 0}, {0, 1}, {0, -1}};
        for (auto& step : d) {
            int nx = x + step[0], ny = y + step[1];
            if (nx >= 0 && nx < 9 && ny >= 0 && ny < 9) NEIGH[i][n++] = ny * 9 + nx;
        }
        NCOUNT[i] = n;
    }
}

static uint64_t hash_board(const uint8_t* board) {
    uint64_t h = 0;
    for (int i = 0; i < 81; ++i) {
        if (board[i] == 1) h ^= W.zblack[i];
        else if (board[i] == 2) h ^= W.zwhite[i];
    }
    return h;
}

static bool seen_has(const vector<uint64_t>& seen, uint64_t h) {
    return find(seen.begin(), seen.end(), h) != seen.end();
}

static void group_at(const uint8_t* board, int start, vector<int>& stones, int& libs) {
    stones.clear();
    uint8_t color = board[start];
    int stack[81];
    bool vis[81]{};
    bool lib_seen[81]{};
    int sp = 0;
    stack[sp++] = start;
    vis[start] = true;
    libs = 0;
    while (sp) {
        int p = stack[--sp];
        stones.push_back(p);
        for (int k = 0; k < NCOUNT[p]; ++k) {
            int n = NEIGH[p][k];
            if (board[n] == 0) {
                if (!lib_seen[n]) { lib_seen[n] = true; ++libs; }
            } else if (board[n] == color && !vis[n]) {
                vis[n] = true;
                stack[sp++] = n;
            }
        }
    }
}

static int place(State& s, int index) {
    if (s.finished || s.board[index] != 0) return -1;
    uint8_t b[81];
    memcpy(b, s.board, 81);
    b[index] = (uint8_t)s.to_play;
    int opp = 3 - s.to_play;
    bool cleared[81]{};
    int captured = 0;
    for (int k = 0; k < NCOUNT[index]; ++k) {
        int n = NEIGH[index][k];
        if (b[n] != opp || cleared[n]) continue;
        vector<int> stones;
        int libs = 0;
        group_at(b, n, stones, libs);
        if (libs) continue;
        for (int stone : stones) {
            b[stone] = 0;
            cleared[stone] = true;
            ++captured;
        }
    }
    vector<int> own;
    int own_libs = 0;
    group_at(b, index, own, own_libs);
    if (!own_libs) return -1;
    uint64_t h = hash_board(b);
    if (seen_has(s.seen, h)) return -1;
    memcpy(s.board, b, 81);
    s.taken[s.to_play] += captured;
    s.hash = h;
    s.seen.push_back(h);
    s.to_play = opp;
    s.passes = 0;
    s.move_count += 1;
    s.last = index;
    return captured;
}

static void pass_move(State& s) {
    s.passes += 1;
    s.to_play = 3 - s.to_play;
    s.move_count += 1;
    s.last = PASS;
    int need = s.passes_to_score > 1 ? s.passes_to_score : 2;
    if (s.passes >= need) {
        s.finished = true;
        bool visited[81]{};
        int black_territory = 0, white_territory = 0, black_stones = 0, white_stones = 0;
        for (int i = 0; i < 81; ++i) {
            if (s.board[i] == 1) ++black_stones;
            else if (s.board[i] == 2) ++white_stones;
        }
        for (int start = 0; start < 81; ++start) {
            if (s.board[start] != 0 || visited[start]) continue;
            int stack[81], sp = 0, region = 0, borders = 0;
            stack[sp++] = start;
            visited[start] = true;
            while (sp) {
                int p = stack[--sp];
                ++region;
                for (int k = 0; k < NCOUNT[p]; ++k) {
                    int n = NEIGH[p][k];
                    if (s.board[n] == 0 && !visited[n]) {
                        visited[n] = true;
                        stack[sp++] = n;
                    } else if (s.board[n] == 1) borders |= 1;
                    else if (s.board[n] == 2) borders |= 2;
                }
            }
            if (borders == 1) black_territory += region;
            else if (borders == 2) white_territory += region;
        }
        double diff = (black_stones + black_territory) - (white_stones + white_territory) - 7.5;
        s.winner = diff > 0 ? 1 : 2;
    }
}

static void encode(const State& s, const bool legal[82], float* feat) {
    int me = s.to_play, opp = 3 - me;
    int libs[81]{};
    bool seen[81]{};
    for (int start = 0; start < 81; ++start) {
        if (!s.board[start] || seen[start]) continue;
        vector<int> stones;
        int count = 0;
        group_at(s.board, start, stones, count);
        for (int stone : stones) {
            seen[stone] = true;
            libs[stone] = count;
        }
    }
    for (int p = 0; p < 12; ++p)
        for (int i = 0; i < 81; ++i) feat[(p * 9 + i / 9) * 9 + i % 9] = 0;
    for (int i = 0; i < 81; ++i) {
        int y = i / 9, x = i % 9;
        auto at = [&](int plane, float v) { feat[(plane * 9 + y) * 9 + x] = v; };
        at(0, s.board[i] == me ? 1.f : 0.f);
        at(1, s.board[i] == opp ? 1.f : 0.f);
        at(2, s.board[i] == 0 ? 1.f : 0.f);
        at(3, legal[i] ? 1.f : 0.f);
        int L = libs[i];
        if (L == 1) at(4, 1);
        else if (L == 2) at(5, 1);
        else if (L == 3) at(6, 1);
        else if (L >= 4) at(7, 1);
        if (s.last == i) at(8, 1);
        at(10, float(s.move_count) / 81.f);
        at(11, 7.5f / 15.f);
    }
    for (int i = 0; i < 81; ++i) {
        if (s.board[i]) continue;
        uint8_t b[81];
        memcpy(b, s.board, 81);
        b[i] = (uint8_t)s.to_play;
        int opp = 3 - s.to_play;
        bool cleared[81]{};
        for (int k = 0; k < NCOUNT[i]; ++k) {
            int n = NEIGH[i][k];
            if (b[n] != opp || cleared[n]) continue;
            vector<int> stones;
            int libs = 0;
            group_at(b, n, stones, libs);
            if (libs) continue;
            for (int stone : stones) { b[stone] = 0; cleared[stone] = true; }
        }
        vector<int> own;
        int own_libs = 0;
        group_at(b, i, own, own_libs);
        if (!own_libs) continue;
        if (seen_has(s.seen, hash_board(b))) {
            int y = i / 9, x = i % 9;
            feat[(9 * 9 + y) * 9 + x] = 1.f;
        }
    }
}

struct Node {
    float prior = 0;
    int visit = 0;
    float value_sum = 0;
    State state;
    bool expanded = false;
    vector<int> moves;
    vector<int> child;
};

static bool farm_enabled() {
    const char* flag = getenv("GO9_FARM");
    return !(flag && flag[0] == '0');
}

static int sim_limit() {
    const char* flag = getenv("GO9_SIMS");
    return flag ? max(1, atoi(flag)) : 32;
}

static double think_seconds() {
    const char* flag = getenv("GO9_SECONDS");
    return flag ? atof(flag) : 2.5;
}

static int biggest_reply_capture(const State& s) {
    int threat = 0;
    for (int i = 0; i < 81; ++i) {
        State trial = s;
        int got = place(trial, i);
        if (got > threat) threat = got;
    }
    return threat;
}

// 提子后自己的棋若马上被回提且回提不少于本手所提，这手不算安全提子。
static bool capture_keeps_stones(const State& root, int move) {
    State after = root;
    int captured = place(after, move);
    if (captured <= 0) return false;
    vector<int> own;
    int libs = 0;
    group_at(after.board, move, own, libs);
    if (libs >= 2) return true;
    return biggest_reply_capture(after) < (int)own.size();
}

static float value_for(const State& s, int me) {
    if (s.finished) return s.winner == me ? 1.f : -1.f;
    bool legal[82]{};
    for (int i = 0; i < 81; ++i) {
        State trial = s;
        if (place(trial, i) >= 0) legal[i] = true;
    }
    legal[PASS] = true;
    float feat[12 * 81], logits[82], value = 0;
    encode(s, legal, feat);
    forward(feat, logits, value);
    return s.to_play == me ? value : -value;
}

static int SPIRAL_RANK[81];

static void init_spiral() {
    int top = 0, left = 0, bottom = 8, right = 8, count = 0;
    int order[81];
    while (top <= bottom && left <= right) {
        for (int x = left; x <= right; ++x) order[count++] = top * 9 + x;
        for (int y = top + 1; y <= bottom; ++y) order[count++] = y * 9 + right;
        if (bottom > top)
            for (int x = right - 1; x >= left; --x) order[count++] = bottom * 9 + x;
        if (right > left)
            for (int y = bottom - 1; y > top; --y) order[count++] = y * 9 + left;
        ++top;
        ++left;
        --bottom;
        --right;
    }
    for (int i = 0; i < count; ++i) SPIRAL_RANK[order[i]] = i;
}

static bool pure_eye(const uint8_t* board, int point, int me) {
    if (board[point] != 0 || NCOUNT[point] == 0) return false;
    for (int k = 0; k < NCOUNT[point]; ++k)
        if (board[NEIGH[point][k]] != me) return false;
    return true;
}

static void collect_groups(const uint8_t* board, int color, vector<vector<int>>& stone_groups, vector<vector<int>>& lib_groups) {
    bool seen[81]{};
    for (int start = 0; start < 81; ++start) {
        if (board[start] != color || seen[start]) continue;
        vector<int> stones, libs;
        bool lib_seen[81]{};
        int stack[81];
        int sp = 0;
        stack[sp++] = start;
        seen[start] = true;
        while (sp) {
            int point = stack[--sp];
            stones.push_back(point);
            for (int k = 0; k < NCOUNT[point]; ++k) {
                int near = NEIGH[point][k];
                if (board[near] == 0) {
                    if (!lib_seen[near]) {
                        lib_seen[near] = true;
                        libs.push_back(near);
                    }
                } else if (board[near] == color && !seen[near]) {
                    seen[near] = true;
                    stack[sp++] = near;
                }
            }
        }
        stone_groups.push_back(std::move(stones));
        lib_groups.push_back(std::move(libs));
    }
}

static bool group_enclosed(const uint8_t* board, const vector<int>& stones, const vector<int>& libs, int color) {
    bool stone[81]{}, lib[81]{};
    for (int point : stones) stone[point] = true;
    for (int point : libs) lib[point] = true;
    for (int point : libs) {
        for (int k = 0; k < NCOUNT[point]; ++k) {
            int near = NEIGH[point][k];
            if (board[near] == 0 && !lib[near]) return false;
            if (board[near] == color && !stone[near]) return false;
        }
    }
    return true;
}

// 先把己方连成一圈。对方整块已被围住且还有两气以上时，不填那两气，让它再走一手，下一手再提。
static int choose_wipe(const State& root) {
    int me = root.to_play;
    int opp = 3 - me;
    bool opponent_left = false;
    bool we_have_stone = false;
    for (int i = 0; i < 81; ++i) {
        if (root.board[i] == opp) opponent_left = true;
        if (root.board[i] == me) we_have_stone = true;
    }
    vector<vector<int>> our_stones, our_libs, opp_stones, opp_libs;
    collect_groups(root.board, me, our_stones, our_libs);
    collect_groups(root.board, opp, opp_stones, opp_libs);
    bool hold[81]{};
    for (size_t g = 0; g < opp_stones.size(); ++g) {
        if (opp_libs[g].size() >= 2 && group_enclosed(root.board, opp_stones[g], opp_libs[g], opp)) {
            for (int point : opp_libs[g]) hold[point] = true;
        }
    }
    bool adjacent[81]{};
    for (const auto& stones : our_stones) {
        for (int stone : stones) {
            for (int k = 0; k < NCOUNT[stone]; ++k) {
                int near = NEIGH[stone][k];
                if (root.board[near] == 0) adjacent[near] = true;
            }
        }
    }
    int best_move = -1;
    float best_score = -1e30f;
    for (int point = 0; point < 81; ++point) {
        State after = root;
        int captured = place(after, point);
        if (captured < 0) continue;
        vector<int> own;
        int own_libs = 0;
        group_at(after.board, point, own, own_libs);
        int reply = 0;
        if (own_libs <= 2 || captured > 0) reply = biggest_reply_capture(after);
        if (reply > captured && own_libs <= 1) continue;
        int need = root.passes_to_score > 1 ? root.passes_to_score : 2;
        if (hold[point] && captured == 0 && root.passes + 2 < need) continue;
        if (pure_eye(root.board, point, me) && opponent_left && captured == 0) continue;
        bool connected = adjacent[point] || !we_have_stone;
        if (!connected && captured == 0) continue;
        vector<vector<int>> next_stones, next_libs;
        collect_groups(after.board, opp, next_stones, next_libs);
        int min_libs = 8;
        for (const auto& libs : next_libs) min_libs = min(min_libs, (int)libs.size());
        float score = captured * 5000.f;
        if (min_libs == 1 && own_libs >= 2 && reply <= captured) score += 2500.f;
        score += max(0, 3 - min_libs) * 400.f;
        score += 5000.f - float(SPIRAL_RANK[point]);
        score += own_libs;
        score -= reply * 300.f;
        if (root.passes && hold[point]) score += 300.f;
        if (score > best_score) {
            best_score = score;
            best_move = point;
        }
    }
    if (best_move < 0) return PASS;
    return best_move;
}

static int yield_again(const State& root, int planned);

static int choose_search(State root, const vector<int>& root_legal) {
    bool legal[82]{};
    for (int m : root_legal) legal[m] = true;
    float feat[12 * 81], logits[82];
    float value = 0;
    encode(root, legal, feat);
    forward(feat, logits, value);
    vector<Node> nodes;
    nodes.push_back(Node());
    nodes[0].state = root;
    nodes[0].visit = 1;
    nodes[0].value_sum = value;
    nodes[0].expanded = true;
    float max_logit = -1e30f;
    for (int m : root_legal) max_logit = max(max_logit, logits[m]);
    float sum = 0;
    vector<float> prior(82, 0);
    vector<int> root_cap(82, 0);
    for (int m : root_legal) {
        if (m == PASS) continue;
        State trial = root;
        int got = place(trial, m);
        if (got > 0) root_cap[m] = got;
    }
    for (int m : root_legal) {
        prior[m] = exp(logits[m] - max_logit);
        sum += prior[m];
    }
    for (int m : root_legal) {
        Node child;
        child.prior = sum > 0 ? prior[m] / sum : 1.f / root_legal.size();
        child.state = root;
        bool ok = m == PASS ? (pass_move(child.state), true) : place(child.state, m) >= 0;
        if (!ok) continue;
        nodes.push_back(std::move(child));
        nodes[0].child.push_back((int)nodes.size() - 1);
        nodes[0].moves.push_back(m);
    }
    auto t0 = chrono::steady_clock::now();
    const double budget = think_seconds();
    for (int sim = 0; sim < sim_limit(); ++sim) {
        if (chrono::duration<double>(chrono::steady_clock::now() - t0).count() > budget) break;
        int id = 0;
        vector<int> path;
        while (nodes[id].expanded && !nodes[id].state.finished && !nodes[id].child.empty()) {
            int parent_n = max(1, nodes[id].visit);
            float sqrt_n = sqrt((float)parent_n);
            int best = nodes[id].child[0];
            float best_score = -1e30f;
            for (int cid : nodes[id].child) {
                Node& ch = nodes[cid];
                float mean = ch.visit ? ch.value_sum / ch.visit : 0;
                float u = 1.1f * ch.prior * sqrt_n / (1 + ch.visit);
                float score = -mean + u;
                if (score > best_score) { best_score = score; best = cid; }
            }
            path.push_back(best);
            id = best;
        }
        float leaf = 0;
        if (nodes[id].state.finished) {
            const State& end = nodes[id].state;
            leaf = end.winner == 0 ? 0.f : (end.winner == end.to_play ? 1.f : -1.f);
        } else if (!nodes[id].expanded) {
            State& st = nodes[id].state;
            bool child_legal[82]{};
            vector<int> moves;
            if (!st.finished) {
                for (int i = 0; i < 81; ++i) {
                    State trial = st;
                    if (place(trial, i) >= 0) {
                        child_legal[i] = true;
                        moves.push_back(i);
                    }
                }
                moves.push_back(PASS);
                child_legal[PASS] = true;
            }
            State base = st;
            float f[12 * 81], lg[82], val = 0;
            encode(base, child_legal, f);
            forward(f, lg, val);
            leaf = val;
            nodes[id].expanded = true;
            float mx = -1e30f;
            for (int m : moves) mx = max(mx, lg[m]);
            float z = 0;
            for (int m : moves) z += exp(lg[m] - mx);
            for (int m : moves) {
                Node ch;
                ch.prior = z > 0 ? exp(lg[m] - mx) / z : 1.f / moves.size();
                ch.state = base;
                bool ok = m == PASS ? (pass_move(ch.state), true) : place(ch.state, m) >= 0;
                if (!ok) continue;
                nodes.push_back(std::move(ch));
                nodes[id].child.push_back((int)nodes.size() - 1);
                nodes[id].moves.push_back(m);
            }
        }
        for (int i = (int)path.size() - 1; i >= 0; --i) {
            Node& ch = nodes[path[i]];
            ch.visit += 1;
            ch.value_sum += leaf;
            leaf = -leaf;
        }
        nodes[0].visit += 1;
    }
    int best_i = 0;
    int best_visit = -1;
    float best_our = -1e30f;
    for (size_t i = 0; i < nodes[0].child.size(); ++i) {
        Node& ch = nodes[nodes[0].child[i]];
        int vis = ch.visit;
        if (vis > best_visit) {
            best_visit = vis;
            best_i = (int)i;
            float mean = vis ? ch.value_sum / vis : 0;
            best_our = -mean;
        }
    }
    int quiet = nodes[0].moves.empty() ? root_legal[0] : nodes[0].moves[best_i];
    if (!farm_enabled()) return quiet;
    if (nodes[0].moves.empty() || best_our < 0.20f) return yield_again(root, quiet);
    int me = root.to_play;
    int farm_move = quiet;
    int farm_cap = root_cap[quiet];
    float farm_value = best_our;
    int evals = 0;
    for (size_t i = 0; i < nodes[0].child.size(); ++i) {
        int move = nodes[0].moves[i];
        int cap = move == PASS ? 0 : root_cap[move];
        if (cap <= 0 || cap < farm_cap || !capture_keeps_stones(root, move)) continue;
        Node& ch = nodes[nodes[0].child[i]];
        float our = -1.f;
        bool accept = false;
        float window = best_our >= 0.55f ? 0.25f : 0.10f;
        int need_visits = best_our >= 0.55f ? 2 : 4;
        if (ch.visit >= need_visits) {
            our = -(ch.value_sum / ch.visit);
            accept = our >= 0.05f && our >= best_our - window;
        } else if (root.move_count >= 55 && best_our >= 0.30f && evals < 4) {
            our = value_for(ch.state, me);
            ++evals;
            accept = our >= 0.20f;
        }
        if (!accept) continue;
        if (cap > farm_cap || (cap == farm_cap && our > farm_value)) {
            farm_cap = cap;
            farm_value = our;
            farm_move = move;
        }
    }
    if (getenv("GO9_TRACE")) {
        cerr << "quiet " << quiet << " best " << best_our << " farm " << farm_move
             << " cap " << farm_cap << " v " << farm_value << "\n";
    }
    return yield_again(root, farm_move);
}

// 对方整块已被围住且还有至少两气时，不填那几气。终局要连续过手 passes_to_score 次，
// 所以可以反复让一手；再让就会被对方连着过手终局时，才自己动手收气。
static int yield_again(const State& root, int planned) {
    int limit = root.passes_to_score > 1 ? root.passes_to_score : 2;
    int me = root.to_play;
    int opp = 3 - me;
    bool opponent_left = false;
    for (int i = 0; i < 81; ++i) if (root.board[i] == opp) opponent_left = true;
    if (!opponent_left || planned < 0) return planned;
    vector<vector<int>> stones, libs;
    collect_groups(root.board, opp, stones, libs);
    bool hold[81]{};
    bool any_hold = false;
    for (size_t g = 0; g < stones.size(); ++g) {
        if (libs[g].size() < 2 || !group_enclosed(root.board, stones[g], libs[g], opp)) continue;
        any_hold = true;
        for (int point : libs[g]) hold[point] = true;
    }
    if (!any_hold) return planned;
    bool room = root.passes + 2 < limit;
    if (room) {
        if (planned == PASS) return PASS;
        if (!hold[planned]) return planned;
        State after = root;
        int captured = place(after, planned);
        if (captured > 0) return planned;
        return PASS;
    }
    if (planned != PASS) return planned;
    for (int point = 0; point < 81; ++point) {
        if (!hold[point]) continue;
        State after = root;
        if (place(after, point) >= 0) return point;
    }
    return planned;
}

struct Json;

struct Json {
    enum Type { NUL, NUM, STR, BOOL, ARR, OBJ } type = NUL;
    double num = 0;
    bool b = false;
    string str;
    vector<Json> arr;
    vector<pair<string, Json>> obj;
    const Json* get(const string& key) const {
        for (auto& item : obj) if (item.first == key) return &item.second;
        return nullptr;
    }
};

struct Parser {
    const string& s;
    size_t i = 0;
    explicit Parser(const string& in) : s(in) {}
    void skip() { while (i < s.size() && isspace((unsigned char)s[i])) ++i; }
    Json parse() { skip(); return value(); }
    Json value() {
        skip();
        if (i >= s.size()) return {};
        char c = s[i];
        if (c == '{') return object();
        if (c == '[') return array();
        if (c == '"') { Json v; v.type = Json::STR; v.str = str(); return v; }
        if (c == 't' || c == 'f') { Json v; v.type = Json::BOOL; v.b = boolean(); return v; }
        if (c == 'n') { i += 4; return {}; }
        Json v; v.type = Json::NUM; v.num = number(); return v;
    }
    Json object() {
        Json v; v.type = Json::OBJ; ++i;
        skip();
        if (i < s.size() && s[i] == '}') { ++i; return v; }
        while (i < s.size()) {
            skip();
            string key = str();
            skip();
            if (i < s.size() && s[i] == ':') ++i;
            v.obj.push_back({key, value()});
            skip();
            if (i < s.size() && s[i] == ',') { ++i; continue; }
            if (i < s.size() && s[i] == '}') { ++i; break; }
            break;
        }
        return v;
    }
    Json array() {
        Json v; v.type = Json::ARR; ++i;
        skip();
        if (i < s.size() && s[i] == ']') { ++i; return v; }
        while (i < s.size()) {
            v.arr.push_back(value());
            skip();
            if (i < s.size() && s[i] == ',') { ++i; continue; }
            if (i < s.size() && s[i] == ']') { ++i; break; }
            break;
        }
        return v;
    }
    string str() {
        if (i < s.size() && s[i] == '"') ++i;
        string out;
        while (i < s.size() && s[i] != '"') {
            if (s[i] == '\\' && i + 1 < s.size()) { out.push_back(s[i + 1]); i += 2; continue; }
            out.push_back(s[i++]);
        }
        if (i < s.size() && s[i] == '"') ++i;
        return out;
    }
    bool boolean() {
        if (s.compare(i, 4, "true") == 0) { i += 4; return true; }
        i += 5; return false;
    }
    double number() {
        size_t start = i;
        if (s[i] == '-') ++i;
        while (i < s.size() && (isdigit((unsigned char)s[i]) || s[i] == '.')) ++i;
        return stod(s.substr(start, i - start));
    }
};

static int our_index(int row, int col) { return (8 - row) * 9 + col; }

static string escape_id(const string& id) {
    string out;
    for (char c : id) {
        if (c == '\\' || c == '"') out.push_back('\\');
        out.push_back(c);
    }
    return out;
}

static vector<uint64_t> history = {0};

static int observation_pass_threshold(const Json& obs) {
    if (const Json* need = obs.get("passes_to_score")) {
        if (need->type == Json::NUM && need->num >= 2 && need->num <= 81 &&
            floor(need->num) == need->num) return (int)need->num;
    }
    return 2;
}

static string respond(const string& line) {
    Json req = Parser(line).parse();
    const Json* obs = req.get("observation");
    string id = req.get("request_id") ? req.get("request_id")->str : "";
    auto reply = [&](const string& action) {
        return string("{\"protocol\":\"arena.v1\",\"request_id\":\"") + escape_id(id) +
               "\",\"type\":\"move_response\",\"action\":" + action + "}";
    };
    if (!obs) return reply("{\"kind\":\"PASS\"}");
    State state;
    if (const Json* board = obs->get("board")) {
        for (int row = 0; row < 9 && row < (int)board->arr.size(); ++row) {
            const Json& line_json = board->arr[row];
            for (int col = 0; col < 9 && col < (int)line_json.arr.size(); ++col) {
                state.board[our_index(row, col)] = (uint8_t)line_json.arr[col].num;
            }
        }
    }
    state.to_play = obs->get("current_player") ? (int)obs->get("current_player")->num : 1;
    state.move_count = obs->get("move_number") ? (int)obs->get("move_number")->num : 0;
    state.passes = obs->get("pass_count") ? (int)obs->get("pass_count")->num : 0;
    state.passes_to_score = observation_pass_threshold(*obs);
    state.hash = hash_board(state.board);
    if (const Json* caps = obs->get("captures")) {
        if (const Json* black = caps->get("1")) state.taken[1] = (int)black->num;
        if (const Json* white = caps->get("2")) state.taken[2] = (int)white->num;
    }
    if (history.empty() || history.back() != state.hash) history.push_back(state.hash);
    state.seen = history;
    if (const Json* last = obs->get("last_action")) {
        if (last->type == Json::OBJ) {
            const Json* kind = last->get("kind");
            if (kind && kind->str == "PLACE" && last->get("row") && last->get("col")) {
                state.last = our_index((int)last->get("row")->num, (int)last->get("col")->num);
            }
        }
    }
    bool pass_blocked = obs->get("pass_forbidden_after_dispute") && obs->get("pass_forbidden_after_dispute")->b;
    vector<int> legal;
    if (const Json* moves = obs->get("legal_moves")) {
        for (const Json& item : moves->arr) {
            if (item.type == Json::ARR && item.arr.size() >= 2) {
                legal.push_back(our_index((int)item.arr[0].num, (int)item.arr[1].num));
            } else if (item.type == Json::OBJ && item.get("row") && item.get("col")) {
                legal.push_back(our_index((int)item.get("row")->num, (int)item.get("col")->num));
            }
        }
    }
    if (!pass_blocked) legal.push_back(PASS);
    if (legal.empty()) return reply("{\"kind\":\"PASS\"}");
    int move = PASS;
    if (W.stem_w.empty()) {
        move = legal[0] == PASS && legal.size() > 1 ? legal[1] : legal[0];
    } else {
        move = choose_search(state, legal);
    }
    bool allowed = find(legal.begin(), legal.end(), move) != legal.end();
    if (!allowed) move = legal[0];
    if (move == PASS) return reply("{\"kind\":\"PASS\"}");
    int y = move / 9, x = move % 9;
    int row = 8 - y, col = x;
    State after = state;
    if (place(after, move) >= 0) {
        if (history.empty() || history.back() != after.hash) history.push_back(after.hash);
    }
    return reply("{\"kind\":\"PLACE\",\"row\":" + to_string(row) + ",\"col\":" + to_string(col) + "}");
}

int main(int argc, char** argv) {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    init_neighbors();
    init_spiral();
    if (!load_weights()) cerr << "weights.txt 没有载入\n";
    if (argc > 1 && string(argv[1]) == "--logits") {
        float feat[12 * 81], logits[82], value = 0;
        for (int i = 0; i < 12 * 81; ++i) cin >> feat[i];
        forward(feat, logits, value);
        cout << value << "\n";
        for (int i = 0; i < 82; ++i) cout << logits[i] << (i + 1 == 82 ? "\n" : " ");
        return 0;
    }
    string line;
    while (getline(cin, line)) {
        if (line.empty()) continue;
        cout << respond(line) << "\n" << flush;
    }
    return 0;
}
